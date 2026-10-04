import importlib.util
import ssl
from pathlib import Path
import shutil
import tempfile
import urllib.error
import unittest
from unittest.mock import patch
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import release_network_retry
from release_network_retry import NetworkUnavailable, RetryBudget, retry_network_read
SPEC = importlib.util.spec_from_file_location('artifact_cache', ROOT / 'scripts/prepared_artifact_cache.py')
CACHE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CACHE)


class ArtifactCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'source.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('receipt.json', 'verified receipt')
        self.digest = CACHE.checksum(self.archive)
        self.downloads = 0

    def test_github_recovery_and_later_ssh_outage_share_one_retry_budget(self):
        state = self.root / 'retry-budget.state'
        now = [0.0]
        events = []
        github_calls = [0]

        def github_read():
            github_calls[0] += 1
            if github_calls[0] == 1:
                raise TimeoutError('temporary GitHub API timeout')
            return 'main-sha'

        github_budget = RetryBudget(clock=lambda: now[0], state_path=str(state))
        self.assertEqual(retry_network_read(
            github_read, 'github_api', lambda error: isinstance(error, TimeoutError),
            budget=github_budget, clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            jitter=lambda _low, high: high, emit=events.append), 'main-sha')
        self.assertEqual(state.read_text().strip(), '1')

        ssh_calls = [0]

        def ssh_read():
            ssh_calls[0] += 1
            if ssh_calls[0] == 1:
                raise ConnectionError('temporary SSH connection timeout')
            return 'readonly baseline'

        ssh_budget = RetryBudget(clock=lambda: now[0], state_path=str(state))
        self.assertEqual(ssh_budget.deadline - now[0], 1799)
        self.assertEqual(retry_network_read(
            ssh_read, 'ssh_pre_exec', lambda error: isinstance(error, ConnectionError),
            budget=ssh_budget, clock=lambda: now[0],
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            jitter=lambda _low, high: high, emit=events.append), 'readonly baseline')
        self.assertEqual(state.read_text().strip(), '2')
        self.assertEqual(github_calls[0], 2)
        self.assertEqual(ssh_calls[0], 2)

    def test_later_stage_exhausts_only_the_remaining_shared_budget(self):
        state = self.root / 'retry-budget.state'
        state.write_text('1799.5\n')
        now = [100.0]
        budget = RetryBudget(clock=lambda: now[0], state_path=str(state))
        self.assertEqual(budget.deadline - now[0], 0.5)
        with self.assertRaises(NetworkUnavailable):
            retry_network_read(
                lambda: (_ for _ in ()).throw(TimeoutError('temporary TLS outage')),
                'github_api', lambda error: isinstance(error, TimeoutError), budget=budget,
                clock=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
                jitter=lambda _low, _high: 60, emit=lambda _message: None)
        self.assertEqual(state.read_text().strip(), '1800')

    def fetch(self, target):
        self.downloads += 1
        shutil.copyfile(self.archive, target)

    def restore(self, artifact_id='12', digest=None):
        return CACHE.materialize(self.root / 'cache', self.root / 'destination', artifact_id,
                                 digest or self.digest, ['receipt.json'], self.fetch)

    def test_second_job_reuses_zip_but_checks_bytes_again(self):
        self.assertEqual(self.restore(), 'MISS')
        self.assertEqual(self.restore(), 'HIT')
        self.assertEqual(self.downloads, 1)
        (self.root / 'cache' / f'12-{self.digest}.zip').write_bytes(b'corrupt cache')
        self.assertEqual(self.restore(), 'MISS')
        self.assertEqual(self.downloads, 2)

    def test_changed_artifact_id_cannot_reuse_another_attempt(self):
        self.restore('12')
        self.assertEqual(self.restore('13'), 'MISS')
        self.assertEqual(self.downloads, 2)

    def test_checksum_failure_is_not_admitted_or_extracted(self):
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            self.restore(digest='0' * 64)
        self.assertFalse((self.root / 'destination').exists())
        self.assertEqual(list((self.root / 'cache').glob('*.zip')), [])

    def test_unexpected_and_traversal_members_are_rejected(self):
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('../escaped', 'unsafe')
        self.digest = CACHE.checksum(self.archive)
        with self.assertRaisesRegex(RuntimeError, 'members'):
            self.restore()
        self.assertFalse((self.root / 'escaped').exists())

    def test_expired_artifact_blocks_even_with_cached_bytes(self):
        self.restore()
        env = dict(GITHUB_REPOSITORY='akhripunov/gpoint', CANDIDATE_SHA='a' * 40,
                   PREPARE_RUN_ID='1', PREPARE_RUN_ATTEMPT='1', PREPARE_ARTIFACT_ID='12',
                   GITHUB_TOKEN='unused', ARTIFACT_DESTINATION=str(self.root / 'destination'))
        run = dict(status='completed', conclusion='success', head_sha='a' * 40,
                   head_branch='main', event='push', path='.github/workflows/gpoint-prepare.yml', run_attempt=1)
        def api(repo, route, token, stage):
            if route == 'actions/runs/1': return run
            if route == 'git/ref/heads/main': return {'object': {'sha': 'a' * 40}}
            return {'expired': True}
        with patch.dict(CACHE.os.environ, env), patch.object(CACHE, 'github', api):
            with self.assertRaisesRegex(RuntimeError, 'selected Prepare'):
                CACHE.main()

    def test_cache_is_bounded_without_touching_other_files(self):
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / 'operator-notes.txt').write_text('keep')
        for i in range(10, 15): self.restore(str(i))
        self.assertEqual(len(list(cache.glob('*.zip'))), 3)
        self.assertTrue((cache / 'operator-notes.txt').exists())

    def test_long_tls_outage_recovers_inside_30_minute_budget(self):
        now = [0.0]
        calls = []
        events = []
        budget = release_network_retry.RetryBudget(1800, clock=lambda: now[0])
        def request():
            calls.append(now[0])
            if len(calls) <= 27:
                raise urllib.error.URLError(ssl.SSLError('TLS read timeout'))
            return 'verified artifact metadata'
        result = release_network_retry.retry_network_read(
            request, 'artifact_metadata', CACHE._retryable, budget=budget,
            clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            jitter=lambda low, high: high, emit=events.append)
        self.assertEqual(result, 'verified artifact metadata')
        self.assertGreater(now[0], 1200)
        self.assertLess(now[0], 1800)
        self.assertTrue(any(event.startswith('NETWORK_RETRYING') for event in events))
        self.assertTrue(any(event.startswith('NETWORK_RESTORED') for event in events))
        self.assertEqual(events[-1], 'RELEASE_CONTINUING stage=artifact_metadata')

    def test_tls_outage_stops_at_30_minutes_and_permanent_errors_do_not_retry(self):
        now = [0.0]
        events = []
        budget = release_network_retry.RetryBudget(1800, clock=lambda: now[0])
        def request():
            raise urllib.error.URLError(TimeoutError('TLS read timeout'))
        with self.assertRaises(release_network_retry.NetworkUnavailable):
            release_network_retry.retry_network_read(
                request, 'current_main_ref', CACHE._retryable, budget=budget,
                clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
                jitter=lambda low, high: high, emit=events.append)
        self.assertEqual(now[0], 1800)
        self.assertTrue(events[-1].startswith('NETWORK_UNAVAILABLE'))
        invalid_cert = urllib.error.URLError(ssl.SSLCertVerificationError('certificate verify failed'))
        unauthorized = urllib.error.HTTPError('https://api.github.com', 401, 'Unauthorized', {}, None)
        self.assertFalse(CACHE._retryable(invalid_cert))
        self.assertFalse(CACHE._retryable(unauthorized))

    def test_download_refreshes_signed_url_after_transient_storage_failure(self):
        import io
        redirects=[]
        storage=[]
        token='secret-test-token'
        def api_open(request, timeout):
            self.assertEqual(request.get_header('Authorization'), 'Bearer '+token)
            redirects.append(1)
            raise urllib.error.HTTPError(request.full_url,302,'Found',
                {'Location':f'https://storage.example/archive?attempt={len(redirects)}'},None)
        class BrokenBlob(io.BytesIO):
            calls=0
            def read(self,size):
                self.calls+=1
                if self.calls>1:
                    raise TimeoutError('simulated mid-transfer interruption')
                return super().read(size)
        def storage_open(location, timeout):
            self.assertIsInstance(location,str)  # No GitHub Authorization forwarded.
            storage.append(location)
            if len(storage)==1:
                return BrokenBlob(b'partial artifact to discard')
            if location.endswith('attempt=1'):
                raise urllib.error.HTTPError(location,403,'Expired signed URL',{},None)
            return io.BytesIO(b'verified complete artifact')
        def bounded_retry(operation, stage, allow_redirect=False):
            for attempt in range(2):
                try:return operation()
                except urllib.error.HTTPError as exc:
                    if allow_redirect and exc.code==302:return exc
                    raise
                except TimeoutError:
                    if attempt:raise
        with patch.object(CACHE.urllib.request,'build_opener') as opener, \
             patch.object(CACHE.urllib.request,'urlopen',side_effect=storage_open), \
             patch.object(CACHE,'network_request',side_effect=bounded_retry):
            opener.return_value.open.side_effect=api_open
            target=self.root/'refreshed.zip'
            CACHE.download('owner/repo',123,token,target)
        self.assertEqual(target.read_bytes(),b'verified complete artifact')
        self.assertEqual(len(redirects),2)
        self.assertNotEqual(storage[0],storage[1])


    def test_artifact_download_auth_failure_is_not_retried(self):
        with patch.object(CACHE.urllib.request,'build_opener') as opener, \
             patch.object(CACHE.time,'sleep') as sleep:
            opener.return_value.open.side_effect=urllib.error.HTTPError(
                'https://api.github.com/artifact',401,'Unauthorized',{},None)
            with self.assertRaises(CACHE.NetworkStageError):
                CACHE.download('owner/repo',123,'test-token',self.root/'denied.zip')
        self.assertEqual(opener.return_value.open.call_count,1)
        sleep.assert_not_called()

    def test_release_download_uses_release_accept_header_and_strips_auth_on_redirect(self):
        import io
        requests = []
        storage_urls = []
        token = "test-release-token"

        def api_open(request, timeout):
            requests.append(request)
            raise urllib.error.HTTPError(request.full_url, 302, "Found",
                {"Location": "https://blob.example/release.zip?sig=short-lived"}, None)

        def blob_open(location, timeout):
            storage_urls.append(location)
            return io.BytesIO(b"immutable release bytes")

        with patch.object(CACHE.urllib.request, "build_opener") as opener, \
             patch.object(CACHE.urllib.request, "urlopen", side_effect=blob_open), \
             patch.object(CACHE, "network_request", side_effect=lambda op, _stage: op()):
            opener.return_value.open.side_effect = api_open
            target = self.root / "release.zip"
            CACHE.download_release("owner/repo", 72, token, target)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer " + token)
        self.assertEqual(requests[0].get_header("Accept"), "application/octet-stream")
        self.assertEqual(storage_urls, ["https://blob.example/release.zip?sig=short-lived"])
        self.assertEqual(target.read_bytes(), b"immutable release bytes")


if __name__ == '__main__':
    unittest.main()
