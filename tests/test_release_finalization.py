from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def test_finalization_accepts_detached_exact_sha_and_rejects_overwrite(tmp_path):
    remote = tmp_path / 'remote.git'
    git(tmp_path, 'init', '--bare', str(remote))
    repo = tmp_path / 'repo'
    git(tmp_path, 'clone', str(remote), str(repo))
    git(repo, 'config', 'user.name', 'Release test')
    git(repo, 'config', 'user.email', 'release-test@example.invalid')
    (repo / 'file').write_text('candidate')
    git(repo, 'add', 'file')
    git(repo, 'commit', '-m', 'candidate')
    sha = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '--detach', sha)
    source = (ROOT / 'scripts/release_prod.sh').read_text()
    start = source.index('RELEASE_REF="refs/maxibot/release/$TARGET_SHA"')
    end = source.index('say FINAL_RUNTIME_SHA', start)
    script = 'set -Eeuo pipefail\ndie() { echo "$*" >&2; exit 1; }\nsay() { :; }\ntimed_stage() { shift; "$@"; }\nTARGET_SHA=' + sha + '\n' + source[start:end]
    result = subprocess.run(['bash', '-c', script], cwd=repo, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert git(repo, 'ls-remote', '--refs', 'origin', f'refs/maxibot/release/{sha}').split()[0] == sha
    # Idempotent exact candidate finalization also works.
    assert subprocess.run(['bash', '-c', script], cwd=repo, capture_output=True).returncode == 0
    (repo / 'file').write_text('second commit')
    git(repo, 'add', 'file')
    git(repo, 'commit', '-m', 'second')
    other = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'push', '--force', 'origin', f'{other}:refs/maxibot/release/{sha}')
    git(repo, 'checkout', '--detach', sha)
    result = subprocess.run(['bash', '-c', script], cwd=repo, text=True, capture_output=True)
    assert result.returncode != 0
    assert 'another candidate' in result.stderr
    assert git(repo, 'ls-remote', '--refs', 'origin', f'refs/maxibot/release/{sha}').split()[0] == other


def test_wrapper_streams_output_before_child_exit():
    command = "import sys; sys.path.insert(0, 'scripts'); from run_release_workflow import run; run([sys.executable, '-c', 'import time; print(\"EARLY\", flush=True); time.sleep(0.5)'], {})"
    with subprocess.Popen(['python3', '-c', command], cwd=ROOT, text=True, stdout=subprocess.PIPE) as process:
        assert 'START' in process.stdout.readline()
        assert process.stdout.readline().strip() == 'EARLY'
        assert process.poll() is None
        assert 'STAGE_SECONDS_' in process.stdout.read()
        assert process.wait() == 0
