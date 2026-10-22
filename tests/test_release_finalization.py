from pathlib import Path
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def _run_finalization(repo, sha, tmp_path, scenario, *, other_sha="", budget_spent="0", read_scenario=""):
    source = (ROOT / 'scripts/release_prod.sh').read_text()
    network_start = source.index('NETWORK_RETRY_BUDGET_SECONDS=1800')
    network_end = source.index('\n[[ $# -ge 1', network_start)
    start = source.index('RELEASE_REF="refs/maxibot/release/$TARGET_SHA"')
    end = source.index('say FINAL_RUNTIME_SHA', start)
    git_path = shutil.which('git')
    state = tmp_path / 'network-budget'
    state.write_text(budget_spent)
    calls = tmp_path / 'git-calls'
    shim_dir = tmp_path / 'bin'
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / 'git'
    shim.write_text(
        '#!/bin/bash\n'
        'if [[ "$1" == "ls-remote" ]]; then\n'
        '  count=$(($(grep -c "^read$" "$GIT_CALLS" 2>/dev/null || true) + 1))\n'
        '  echo "read" >> "$GIT_CALLS"\n'
        '  case "$READ_SCENARIO:$count" in\n'
        '    tls_once:1) echo "fatal: SSL_ERROR_SYSCALL" >&2; exit 128;;\n'
        '    tls_twice:1|tls_twice:2) echo "fatal: SSL_ERROR_SYSCALL" >&2; exit 128;;\n'
        '    auth_eof:*) echo "fatal: Authentication failed; unexpected EOF" >&2; exit 128;;\n'
        '    cert:*) echo "fatal: unable to access: SSL certificate problem: certificate verify failed" >&2; exit 128;;\n'
        '  esac\n'
        'fi\n'
        'if [[ "$1" == "push" ]]; then\n'
        '  count=$(($(grep -c "^push$" "$GIT_CALLS" 2>/dev/null || true) + 1))\n'
        '  echo "push" >> "$GIT_CALLS"\n'
        '  case "$PUSH_SCENARIO:$count" in\n'
        '    accepted_then_lost:1) "$REAL_GIT" "$@" || exit $?; echo "fatal: SSL_ERROR_SYSCALL" >&2; exit 128;;\n'
        '    fail_once:1) echo "fatal: SSL_ERROR_SYSCALL" >&2; exit 128;;\n'
        '    concurrent_conflict:1) "$REAL_GIT" push origin "$OTHER_SHA:$RELEASE_REF" || exit $?; echo "fatal: connection reset" >&2; exit 128;;\n'
        '    authentication:1) echo "fatal: Authentication failed" >&2; exit 128;;\n'
        '    certificate:1) echo "fatal: SSL certificate problem: certificate verify failed" >&2; exit 128;;\n'
        '  esac\n'
        'fi\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    prefix = (
        'set -Eeuo pipefail\n'
        'die() { echo "RELEASE_BLOCKED=$*" >&2; exit 1; }\n'
        'say() { :; }\n'
        'timed_stage() { local name=$1; shift; "$@"; }\n'
        'TARGET_SHA=' + sha + '\n'
    )
    script = prefix + source[network_start:network_end] + '\n' + source[start:end]
    env = os.environ.copy()
    env.update({
        'PATH': f'{shim_dir}:{env["PATH"]}', 'REAL_GIT': git_path,
        'GIT_CALLS': str(calls), 'PUSH_SCENARIO': scenario,
        'READ_SCENARIO': read_scenario,
        'OTHER_SHA': other_sha, 'NETWORK_RETRY_BUDGET_STATE': str(state),
        'RELEASE_REF': f'refs/maxibot/release/{sha}',
    })
    result = subprocess.run(['bash', '-c', script], cwd=repo, env=env,
                            text=True, capture_output=True)
    log = calls.read_text().splitlines() if calls.exists() else []
    return result, log, state.read_text().strip()


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
    network_start = source.index('NETWORK_RETRY_BUDGET_SECONDS=1800')
    network_end = source.index('\n[[ $# -ge 1', network_start)
    script = ('set -Eeuo pipefail\ndie() { echo "$*" >&2; exit 1; }\nsay() { :; }\n'
              'timed_stage() { shift; "$@"; }\nTARGET_SHA=' + sha + '\n'
              + source[network_start:network_end] + '\n' + source[start:end])
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


def test_ambiguous_push_acknowledgement_is_confirmed_by_same_ref_readback(tmp_path):
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

    result, calls, _budget = _run_finalization(repo, sha, tmp_path, 'accepted_then_lost')

    assert result.returncode == 0, result.stderr
    assert calls.count('push') == 1
    assert git(repo, 'ls-remote', '--refs', 'origin', f'refs/maxibot/release/{sha}').split()[0] == sha


def test_transient_push_failure_retries_only_after_same_ref_is_still_absent(tmp_path):
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

    result, calls, _budget = _run_finalization(repo, sha, tmp_path, 'fail_once')

    assert result.returncode == 0, result.stderr
    assert calls.count('push') == 2
    assert git(repo, 'ls-remote', '--refs', 'origin', f'refs/maxibot/release/{sha}').split()[0] == sha


def test_concurrent_other_sha_after_ambiguous_push_is_never_overwritten(tmp_path):
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
    (repo / 'file').write_text('other')
    git(repo, 'add', 'file')
    git(repo, 'commit', '-m', 'other')
    other = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'checkout', '--detach', sha)

    result, calls, _budget = _run_finalization(repo, sha, tmp_path, 'concurrent_conflict', other_sha=other)

    assert result.returncode != 0
    assert calls.count('push') == 1
    assert 'another candidate' in result.stderr or 'remote release ref' in result.stderr
    assert git(repo, 'ls-remote', '--refs', 'origin', f'refs/maxibot/release/{sha}').split()[0] == other


def test_authentication_failure_does_not_retry_push(tmp_path):
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

    result, calls, _budget = _run_finalization(repo, sha, tmp_path, 'authentication')

    assert result.returncode != 0
    assert calls.count('push') == 1
    assert 'authentication' in result.stderr.lower()


def test_shared_network_retry_budget_stops_before_repeating_push(tmp_path):
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

    result, calls, spent = _run_finalization(repo, sha, tmp_path, 'fail_once', budget_spent='1799')

    assert result.returncode != 0
    assert calls.count('push') == 1
    assert int(spent) >= 1800


def test_git_tls_readback_retries_exit_128_and_confirms_ref(tmp_path):
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

    result, calls, _budget = _run_finalization(repo, sha, tmp_path, 'normal', read_scenario='tls_once')

    assert result.returncode == 0, result.stderr
    assert calls.count('read') >= 3
    assert calls.count('push') == 1


def test_certificate_error_on_readback_is_fatal_without_push(tmp_path):
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

    result, calls, _budget = _run_finalization(repo, sha, tmp_path, 'normal', read_scenario='cert')

    assert result.returncode != 0
    assert calls == ['read']
    assert 'certificate' in result.stderr.lower()


def test_certificate_error_on_push_is_fatal_without_retry(tmp_path):
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

    result, calls, _budget = _run_finalization(repo, sha, tmp_path, 'certificate')

    assert result.returncode != 0
    assert calls.count('push') == 1
    assert 'certificate' in result.stderr.lower()


def test_read_retry_budget_survives_command_substitution_and_later_push(tmp_path):
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

    result, calls, spent = _run_finalization(
        repo, sha, tmp_path, 'fail_once', read_scenario='tls_once')

    assert result.returncode == 0, result.stderr
    assert calls.count('push') == 2
    assert int(spent) >= 2


def test_retry_backoff_exhaustion_prevents_next_push(tmp_path):
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

    result, calls, spent = _run_finalization(repo, sha, tmp_path, 'fail_once', budget_spent='1798')

    assert result.returncode != 0
    assert calls.count('push') == 1
    assert int(spent) >= 1800


def test_multiple_read_failures_charge_elapsed_once_from_entry_snapshot(tmp_path):
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

    result, calls, spent = _run_finalization(
        repo, sha, tmp_path, 'normal', read_scenario='tls_twice', budget_spent='1796')

    assert result.returncode == 0, result.stderr
    assert calls.count('read') >= 4
    assert calls.count('push') == 1
    assert int(spent) <= 1800


def test_authentication_error_with_eof_is_not_retried_on_readback(tmp_path):
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

    result, calls, _budget = _run_finalization(
        repo, sha, tmp_path, 'normal', read_scenario='auth_eof')

    assert result.returncode != 0
    assert calls == ['read']
    assert 'authentication failed' in result.stderr.lower()


def test_wrapper_streams_output_before_child_exit():
    command = "import sys; sys.path.insert(0, 'scripts'); from run_release_workflow import run; run([sys.executable, '-c', 'import time; print(\"EARLY\", flush=True); time.sleep(0.5)'], {})"
    with subprocess.Popen(['python3', '-c', command], cwd=ROOT, text=True, stdout=subprocess.PIPE) as process:
        assert 'START' in process.stdout.readline()
        assert process.stdout.readline().strip() == 'EARLY'
        assert process.poll() is None
        assert 'STAGE_SECONDS_' in process.stdout.read()
        assert process.wait() == 0
