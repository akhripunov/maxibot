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


if __name__ == '__main__':
    unittest.main()
