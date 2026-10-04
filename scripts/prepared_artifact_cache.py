#!/usr/bin/env python3
"""Reuse an immutable Actions ZIP only after fresh GitHub provenance/digest checks."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_network_retry import NetworkUnavailable, RetryBudget, retry_network_read
from release_assets import PREPARE_ASSET_NAME, ReleaseAssetError, release_tag, resolve_asset, validate_prepare_directory


MAX_BYTES = 4 * 1024 ** 3
NETWORK_BUDGET = None


class NetworkStageError(RuntimeError):
    def __init__(self, stage: str, error_type: str) -> None:
        super().__init__(stage, error_type)
        self.stage = stage
        self.error_type = error_type


def _retryable(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code in {408, 425, 429, 500, 502, 503, 504}
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return False
    if isinstance(reason, ssl.SSLError):
        return not any(marker in str(reason).lower() for marker in (
            "certificate_verify_failed", "wrong_version_number", "protocol_version"))
    return isinstance(reason, (TimeoutError, socket.timeout, socket.gaierror, ConnectionError))


def network_request(operation, stage: str, allow_redirect: bool = False):
    def request_once():
        try:
            return operation()
        except urllib.error.HTTPError as error:
            # This one redirect is the expected handoff to signed blob storage.
            if allow_redirect and error.code == 302:
                return error
            raise
    try:
        return retry_network_read(request_once, stage, _retryable, budget=NETWORK_BUDGET)
    except NetworkUnavailable:
        raise NetworkStageError(stage, "NETWORK_UNAVAILABLE") from None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            socket.timeout, ConnectionError, ssl.SSLError) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        raise NetworkStageError(stage, type(reason).__name__) from None


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def github(repo, route, token, stage):
    request = urllib.request.Request(f'https://api.github.com/repos/{repo}/{route}', headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'})
    def fetch():
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    return network_request(fetch, stage)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def download_release(repo, asset_id, token, target):
    """Download a GitHub Release asset without forwarding credentials to storage."""
    def copy_bounded(response):
        with response, target.open('wb') as output:
            count = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > MAX_BYTES:
                    raise RuntimeError('Release asset exceeds the bounded cache size')
                output.write(chunk)

    def fetch_asset():
        request = urllib.request.Request(
            f'https://api.github.com/repos/{repo}/releases/assets/{asset_id}',
            headers={'Authorization': f'Bearer {token}',
                     'Accept': 'application/octet-stream',
                     'X-GitHub-Api-Version': '2022-11-28'})
        try:
            response = urllib.request.build_opener(NoRedirect).open(request, timeout=30)
        except urllib.error.HTTPError as error:
            if error.code != 302:
                raise
            location = error.headers.get('Location', '')
            try:
                error.close()
            except (AttributeError, KeyError):
                pass
            if not location.startswith('https://'):
                raise RuntimeError('Release asset redirect must use HTTPS')
            copy_bounded(urllib.request.urlopen(location, timeout=30))
        else:
            copy_bounded(response)

    network_request(fetch_asset, 'release_asset_download')


def download(repo, artifact_id, token, target):
    # Each bounded attempt obtains a fresh signed URL. Never forward the
    # GitHub token to blob storage or reuse a URL after a connection outage.
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip',
        headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})

    def copy_bounded(response):
        with response, target.open('wb') as output:
            count = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > MAX_BYTES:
                    raise RuntimeError('Artifact exceeds the bounded cache size')
                output.write(chunk)

    def fetch_storage():
        try:
            response = urllib.request.build_opener(NoRedirect).open(request, timeout=30)
        except urllib.error.HTTPError as error:
            if error.code != 302:
                raise
            location = error.headers.get('Location', '')
            try:
                error.close()
            except (AttributeError, KeyError):
                pass
            if not location.startswith('https://'):
                raise RuntimeError('Artifact redirect must use HTTPS')
            copy_bounded(urllib.request.urlopen(location, timeout=30))
        else:
            copy_bounded(response)

    # One retry scope/budget covers redirect plus transfer; no nested charges.
    network_request(fetch_storage, 'artifact_zip_storage')

def extract(archive, destination, expected):
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if {entry.filename for entry in entries} != set(expected) or len(entries) != len(expected):
            raise RuntimeError('Unexpected artifact members')
        if sum(entry.file_size for entry in entries) > MAX_BYTES:
            raise RuntimeError('Expanded artifact exceeds size limit')
        for entry in entries:
            parts = Path(entry.filename).parts
            if (stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir()
                    or entry.filename.startswith(('/', '\\')) or '..' in parts
                    or len(parts) != 1):
                raise RuntimeError('Artifact members must be regular files')
        destination.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            target = destination / entry.filename
            if target.is_symlink():
                raise RuntimeError('Artifact destination must not be a symlink')
            with source.open(entry) as incoming, target.open('wb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)


def materialize(cache, destination, artifact_id, digest, expected, fetch):
    if not re.fullmatch(r'[0-9]+', str(artifact_id)) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise RuntimeError('Invalid immutable artifact identity')
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = cache / f'{artifact_id}-{digest}.zip'
    hit = archive.is_file() and not archive.is_symlink() and checksum(archive) == digest
    if not hit:
        with tempfile.TemporaryDirectory(prefix='incoming-', dir=cache) as temporary:
            pending = Path(temporary) / 'artifact.zip'
            fetch(pending)
            if checksum(pending) != digest:
                raise RuntimeError('GitHub artifact ZIP checksum mismatch')
            # Validate members before admitting downloaded bytes to the cache.
            extract(pending, destination, expected)
            pending.replace(archive)
    else:
        extract(archive, destination, expected)
    archive.touch()
    # Only our immutable ZIPs are evicted. Job files and other projects are untouched.
    older = sorted((p for p in cache.glob('*.zip') if p != archive and not p.is_symlink()
                    and re.fullmatch(r'[0-9]+-[a-f0-9]{64}\.zip', p.name)),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    retained_bytes = archive.stat().st_size
    for index, path in enumerate(older):
        retained_bytes += path.stat().st_size
        if index >= 2 or retained_bytes > 1024 ** 3:
            path.unlink()
    return 'HIT' if hit else 'MISS'


def main():
    global NETWORK_BUDGET
    NETWORK_BUDGET = RetryBudget()
    started = time.monotonic()
    repo = os.environ['GITHUB_REPOSITORY']
    projects = {
        'akhripunov/gpoint': ('gpoint', 'gpoint-prepare.yml', ['images.tar.gz', 'receipt.json']),
        'akhripunov/maxibot': ('maxibot', 'maxibot-prepare.yml', [
            f"maxibot-image-{os.environ['CANDIDATE_SHA']}.tar.gz",
            'maxibot-image-manifest.json', 'maxibot-prepare-result.json']),
    }
    if repo not in projects:
        raise RuntimeError('Unrecognized release repository')
    project, workflow, expected = projects[repo]
    sha, run_id = os.environ['CANDIDATE_SHA'], os.environ['PREPARE_RUN_ID']
    attempt = os.environ['PREPARE_RUN_ATTEMPT']
    if not re.fullmatch(r'[a-f0-9]{40}', sha) or not all(x.isdigit() for x in (run_id, attempt)):
        raise RuntimeError('Invalid release identity')
    token = os.environ['GITHUB_TOKEN']
    stage = 'prepare_run_metadata'
    run = github(repo, f'actions/runs/{run_id}', token, stage)
    if (run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('head_sha') != sha or run.get('head_branch') != 'main'
            or run.get('event') not in ('push', 'workflow_dispatch')
            or run.get('path') != f'.github/workflows/{workflow}'
            or str(run.get('run_attempt')) != attempt):
        raise RuntimeError('Successful Prepare provenance changed')
    stage = 'current_main_ref'
    if github(repo, 'git/ref/heads/main', token, stage)['object']['sha'] != sha:
        raise RuntimeError('Candidate is no longer current main')
    if project == 'maxibot':
        stage = 'immutable_release_metadata'
        tag = release_tag('prepare', sha, run_id, attempt)
        if os.environ.get('PREPARE_RELEASE_TAG') != tag:
            raise RuntimeError('Selected Prepare Release tag differs from exact run identity')
        try:
            metadata = resolve_asset(
                'prepare', sha, run_id, attempt, PREPARE_ASSET_NAME,
                lambda route: github(repo, route, token, stage),
            )
        except ReleaseAssetError as error:
            raise RuntimeError('Immutable Prepare Release provenance changed') from error
        asset_id = str(metadata['id'])
        digest = metadata.get('digest', '')
        if (asset_id != os.environ.get('PREPARE_RELEASE_ASSET_ID')
                or digest != 'sha256:' + os.environ.get('PREPARE_RELEASE_ASSET_SHA256', '')):
            raise RuntimeError('Prepare Release asset identity changed after selection')
        cache_id = asset_id
        fetch = lambda path: download_release(repo, asset_id, token, path)
    else:
        # GPoint continues to use its own established Actions Artifact transport.
        artifact_id = os.environ['PREPARE_ARTIFACT_ID']
        if not artifact_id.isdigit():
            raise RuntimeError('Invalid legacy artifact identity')
        stage = 'artifact_metadata'
        metadata = github(repo, f'actions/artifacts/{artifact_id}', token, stage)
        name = f'gpoint-release-{sha}-{run_id}-{attempt}'
        if (metadata.get('expired') is not False or metadata.get('name') != name
                or str(metadata.get('workflow_run', {}).get('id')) != run_id
                or metadata.get('workflow_run', {}).get('head_sha') != sha):
            raise RuntimeError('Artifact does not belong to the selected Prepare')
        digest = metadata.get('digest', '')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise RuntimeError('GitHub artifact digest is unavailable')
        cache_id = artifact_id
        fetch = lambda path: download(repo, artifact_id, token, path)
    cache = Path.home() / '.cache' / f'{project}-release' / 'artifacts'
    stage = 'artifact_cache_or_download'
    destination = Path(os.environ['ARTIFACT_DESTINATION'])
    result = materialize(cache, destination, cache_id, digest[7:], expected, fetch)
    if project == 'maxibot':
        validate_prepare_directory(destination, sha, run_id, attempt)
        print(f'RELEASE_BUNDLE_CACHE={result} RELEASE_ASSET_ID={cache_id} SHA={sha}', flush=True)
    else:
        print(f'ARTIFACT_CACHE={result} ARTIFACT_ID={cache_id} SHA={sha}', flush=True)
    print(f'STAGE_SECONDS_ARTIFACT_DOWNLOAD={time.monotonic() - started:.3f}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except NetworkStageError as error:
        print(f'ARTIFACT_CACHE=BLOCKED STAGE={error.stage} ERROR_TYPE={error.error_type}', flush=True)
        raise SystemExit(1)
    except Exception as error:
        # Network exception strings may contain signed URLs; never print them.
        print(f'ARTIFACT_CACHE=BLOCKED STAGE={locals().get("stage", "artifact_validation")} ERROR_TYPE={type(error).__name__}', flush=True)
        raise SystemExit(1)
