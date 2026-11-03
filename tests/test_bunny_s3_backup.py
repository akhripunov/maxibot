import os
import subprocess
import hashlib
import signal
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "deploy/backup/maxibot-bunny-backup.sh"
UNIT = ROOT / "deploy/backup/maxibot-bunny-backup.service"
TIMER = ROOT / "deploy/backup/maxibot-bunny-backup.timer"
DRILL_UNIT = ROOT / "deploy/backup/maxibot-bunny-restore-drill.service"
DRILL_TIMER = ROOT / "deploy/backup/maxibot-bunny-restore-drill.timer"
PROVISIONER = ROOT / "deploy/backup/provision-bunny-credentials.sh"
INSTALLER = ROOT / "deploy/backup/install-bunny-backup.sh"


def _bash(source: str, *args: str, env=None):
    return subprocess.run(
        ["bash", "-c", source, "bash-test", *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_restore_container_cleanup_is_fail_closed_for_docker_api_errors(tmp_path):
    scenarios = [
        ("present", "0", 0),
        ("missing", "0", 0),
        ("daemon_error", "0", 1),
        ("present", "1", 1),
        ("present_remains", "0", 1),
        ("daemon_error_after_remove", "0", 1),
    ]
    for inspect_state, remove_status, expected in scenarios:
        env = os.environ | {
            "MOCK_INSPECT_STATE": inspect_state,
            "MOCK_REMOVE_STATUS": remove_status,
            "MOCK_INSPECT_CALLS": "0",
            "TMPDIR": str(tmp_path),
        }
        result = _bash(
            'source "$1"; '
            'docker() { '
            '  if [[ "$1" == inspect ]]; then '
            '    case "$MOCK_INSPECT_STATE" in '
            '      present) MOCK_INSPECT_CALLS=$((MOCK_INSPECT_CALLS + 1)); export MOCK_INSPECT_CALLS; if (( MOCK_INSPECT_CALLS == 1 )); then return 0; else echo "Error: No such object: $2" >&2; return 1; fi ;; '
            '      present_remains) return 0 ;; '
            '      daemon_error_after_remove) MOCK_INSPECT_CALLS=$((MOCK_INSPECT_CALLS + 1)); export MOCK_INSPECT_CALLS; if (( MOCK_INSPECT_CALLS == 1 )); then return 0; else echo "Cannot connect to the Docker daemon" >&2; return 1; fi ;; '
            '      missing) echo "Error: No such object: $2" >&2; return 1 ;; '
            '      daemon_error) echo "Cannot connect to the Docker daemon" >&2; return 1 ;; '
            '    esac; '
            '  fi; '
            '  if [[ "$1" == rm ]]; then return "$MOCK_REMOVE_STATUS"; fi; '
            '  return 99; '
            '}; cleanup_restore_container restore-test',
            str(BACKUP),
            env=env,
        )
        assert result.returncode == expected, (inspect_state, remove_status, result.stderr)


def test_postgres_image_digest_is_immutable_and_restricted_to_official_image():
    valid = (
        "postgres@sha256:" + "a" * 64,
        "postgres:16-alpine@sha256:" + "b" * 64,
        "docker.io/library/postgres:16-alpine@sha256:" + "c" * 64,
    )
    invalid = (
        "attacker.example/postgres:16@sha256:" + "a" * 64,
        "postgres:16-alpine",
        "docker.io/library/not-postgres@sha256:" + "a" * 64,
    )
    for reference, expected in [(value, 0) for value in valid] + [(value, 1) for value in invalid]:
        result = _bash(
            'source "$1"; is_pinned_postgres_image "$2"',
            str(BACKUP),
            reference,
        )
        assert (result.returncode != 0) == bool(expected), reference


def test_uploaded_archive_requires_matching_remote_size_and_readback_hash(tmp_path):
    local_archive = tmp_path / "backup.tar.age"
    local_archive.write_bytes(b"encrypted-backup")
    env = os.environ | {
        "MOCK_REMOTE_OBJECT": str(tmp_path / "remote-object"),
        "MOCK_REMOTE_SHA": hashlib.sha256(local_archive.read_bytes()).hexdigest(),
    }
    (tmp_path / "remote-object").write_bytes(local_archive.read_bytes())
    verify = (
        'source "$1"; '
        'aws_s3() { '
        '  if [[ "$2" == head-object && "$*" == *"--query ContentLength"* ]]; then wc -c < "$MOCK_REMOTE_OBJECT" | tr -d "[:space:]"; '
        '  elif [[ "$2" == head-object && "$*" == *"--query Metadata.sha256"* ]]; then printf "%s\\n" "$MOCK_REMOTE_SHA"; '
        '  elif [[ "$2" == cp ]]; then cat "$MOCK_REMOTE_OBJECT"; '
        '  else return 99; fi; '
        '}; verify_uploaded_archive daily/test.tar.age "$2" '
        '"$(sha256sum "$2" | awk \'{print $1}\')" '
        '"$(wc -c < "$2" | tr -d " ")"'
    )
    good = _bash(verify, str(BACKUP), str(local_archive), env=env)
    assert good.returncode == 0, good.stderr

    (tmp_path / "remote-object").write_bytes(b"tampered-backup")
    bad_hash = _bash(verify, str(BACKUP), str(local_archive), env=env)
    assert bad_hash.returncode != 0

    (tmp_path / "remote-object").write_bytes(local_archive.read_bytes() + b"x")
    bad_size = _bash(verify, str(BACKUP), str(local_archive), env=env)
    assert bad_size.returncode != 0


def test_backup_and_restore_use_private_credentials_and_isolated_restore():
    backup = BACKUP.read_text()
    backup_unit = UNIT.read_text()
    timer = TIMER.read_text()
    drill_unit = DRILL_UNIT.read_text()
    drill_timer = DRILL_TIMER.read_text()

    assert "pg_dump" in backup and "--format=custom" in backup
    assert "pg_dumpall" in backup and "--globals-only" in backup
    assert "docker\\.io/" in backup and "postgres" in backup
    assert "age --recipient" in backup and "--decrypt --identity" in backup
    assert "verify_uploaded_archive" in backup
    assert "LoadCredentialEncrypted=bunny-s3" in backup_unit
    assert "LoadCredentialEncrypted=backup-age-identity" in drill_unit
    assert "--network none" in backup
    assert "--mount \"type=bind,source=$restore_data,target=/var/lib/postgresql/data\"" in backup
    assert "--mount \"type=volume,source=$UPLOADS_VOLUME,target=/source,readonly\"" in backup
    assert "2 * (db_dump_bytes + globals_dump_bytes + uploads_dump_bytes)" in backup
    assert "3 * expected_size + 2 * db_bytes + 2 * uploads_bytes" in backup
    assert "OnCalendar=" in timer and "Persistent=true" in timer
    assert "OnCalendar=" in drill_timer and "Persistent=true" in drill_timer
    assert "/run/lock/maxibot-bunny-backup.lock" in backup_unit
    assert "/run/lock/maxibot-bunny-backup.lock" in drill_unit
    assert "maxibot-vps_maxibot_uploads" in backup
    assert "/opt/maxibot/backups" not in backup
    assert "LoadCredentialEncrypted" not in (ROOT / "deploy/backup/bunny-backup.conf.example").read_text()


def test_daily_backup_timer_runs_at_0600_moscow_with_small_jitter():
    timer = TIMER.read_text()

    assert "OnCalendar=*-*-* 03:00:00 UTC" in timer
    assert "RandomizedDelaySec=5min" in timer
    assert "Persistent=true" in timer


def test_monthly_restore_drill_runs_after_daily_backup_timeout_window():
    timer = DRILL_TIMER.read_text()
    unit = DRILL_UNIT.read_text()

    assert "OnCalendar=*-*-01 08:00:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "Restart=on-failure" in unit
    assert "RestartSec=15min" in unit
    assert "StartLimitIntervalSec=1d" in unit
    assert "StartLimitBurst=3" in unit


def test_backup_and_restore_wait_for_shared_lock_with_bounded_service_timeout():
    for unit_path in (UNIT, DRILL_UNIT):
        unit = unit_path.read_text()
        assert "flock --wait 18000 /run/lock/maxibot-bunny-backup.lock" in unit
        assert "timeout --signal=TERM --kill-after=60s 4h /usr/local/sbin/maxibot-bunny-backup" in unit
        assert "flock -n" not in unit
        assert "TimeoutStartSec=10h" in unit


def test_secret_provisioning_never_overwrites_existing_credentials():
    source = PROVISIONER.read_text()
    assert 'if [[ -e "$destination" ]]' in source
    assert 'ln "$temporary" "$destination"' in source
    assert "systemd-creds encrypt" in source
    assert "echo \"$BUNNY_SECRET\"" not in source
    assert "echo \"$AGE_IDENTITY\"" not in source
    assert "--with-key=host" in source


def test_installer_fails_before_mutation_without_encrypted_credential_support():
    source = INSTALLER.read_text()
    assert "-ge 250" in source
    assert "systemd-creds" in source
    for command in ("python3", "tar", "sha256sum", "aws", "age", "docker", "flock", "timeout"):
        assert command in source
    assert source.index("systemd 250 or newer") < source.index('install -d -o root')


def test_restore_archive_validation_rejects_traversal_links_and_special_files(tmp_path):
    import io
    import tarfile

    cases = {
        "traversal": ("../escape", tarfile.REGTYPE),
        "symlink": ("uploads/link", tarfile.SYMTYPE),
        "fifo": ("uploads/pipe", tarfile.FIFOTYPE),
    }
    for label, (name, member_type) in cases.items():
        archive_path = tmp_path / f"{label}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            info = tarfile.TarInfo(name)
            info.type = member_type
            info.linkname = "/etc/passwd"
            if member_type == tarfile.REGTYPE:
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
            else:
                archive.addfile(info)
        result = _bash(
            'source "$1"; validate_archive_paths "$2"',
            str(BACKUP),
            str(archive_path),
        )
        assert result.returncode != 0, label


def test_temporary_plaintext_is_private_and_cleanup_is_mandatory():
    source = BACKUP.read_text()
    assert "umask 077" in source
    assert "mktemp -d" in source
    assert "cleanup_sensitive_workdir" in source
    assert "trap cleanup EXIT" in source
    assert 'rm -rf -- "$WORK_DIR"' in source
    assert "restore-tested" in source


@pytest.mark.parametrize(
    ("phase", "filename"),
    [
        ("postgres_dump", "partial.dump"),
        ("uploads_archive", "uploads.tar.gz"),
        ("age_encryption", "partial.tar.age"),
        ("bunny_upload", "partial.tar.age"),
        ("isolated_restore", "restore.tar.gz"),
    ],
)
def test_sigterm_runs_exit_cleanup_for_interrupted_sensitive_workspace(tmp_path, phase, filename):
    workdir = tmp_path / "maxibot-bunny.term"
    script = (
        'source "$1"; WORK_DIR="$2"; RESTORE_CONTAINER=""; '
        'MOCK_REMOVED=0; docker() { case "$1" in inspect) if [[ "$MOCK_REMOVED" == 1 ]]; then echo "Error: No such object: $2" >&2; return 1; fi; return 0 ;; rm) MOCK_REMOVED=1; return 0 ;; *) return 90 ;; esac; }; '
        'install_cleanup_traps; mkdir -m 700 "$WORK_DIR"; '
        'printf test-only > "$WORK_DIR/$3"; '
        '[[ "$4" == isolated_restore ]] && RESTORE_CONTAINER=restore-test; '
        'while :; do :; done; echo "PASS: should not be reached"'
    )
    process = subprocess.Popen(
        ["bash", "-c", script, "bash-test", str(BACKUP), str(workdir), filename, phase],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        import time

        deadline = time.monotonic() + 3
        while not (workdir / filename).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (workdir / filename).exists(), "fault-injection phase did not start"
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 143, stderr
        assert not workdir.exists()
        assert "PASS: should not be reached" not in stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_sigkill_leftovers_are_removed_at_next_locked_run(tmp_path):
    workdir = tmp_path / "maxibot-bunny.sigkill"
    unrelated = tmp_path / "unrelated-recovery-copy"
    unrelated.mkdir()
    (unrelated / "keep.dump").write_bytes(b"preserve")
    state = tmp_path / "containers"
    state.write_text("deadbeefdead\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  ps) cat \"$MOCK_DOCKER_STATE\" ;;\n"
        "  rm) : > \"$MOCK_DOCKER_STATE\" ;;\n"
        "  *) exit 90 ;;\n"
        "esac\n"
    )
    docker.chmod(0o755)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MOCK_DOCKER_STATE": str(state),
    }
    script = (
        'source "$1"; WORK_DIR="$2"; RESTORE_CONTAINER=""; '
        'install_cleanup_traps; trap \'while :; do :; done\' TERM; mkdir -m 700 "$WORK_DIR"; '
        'printf test-only > "$WORK_DIR/partial.dump"; '
        'while :; do :; done'
    )
    interrupted = subprocess.Popen(
        ["bash", "-c", script, "bash-test", str(BACKUP), str(workdir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # The child creates a genuinely trap-managed workspace, then SIGKILL
    # bypasses EXIT cleanup as it does for the production script.
    import time

    try:
        time.sleep(0.1)
        interrupted.send_signal(signal.SIGTERM)
        time.sleep(0.1)
        interrupted.kill()
        interrupted.wait(timeout=5)
    finally:
        if interrupted.poll() is None:
            interrupted.kill()
            interrupted.wait(timeout=5)
    assert workdir.exists()

    result = _bash(
        'source "$1"; TEMP_ROOT="$2"; '
        'cleanup_stale_resources',
        str(BACKUP),
        str(tmp_path),
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not workdir.exists()
    assert state.read_text() == ""
    assert (unrelated / "keep.dump").read_bytes() == b"preserve"


def test_stale_resource_cleanup_fails_closed_on_docker_list_error(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexit 42\n")
    docker.chmod(0o755)
    workdir = tmp_path / "maxibot-bunny.must-stay"
    workdir.mkdir(mode=0o700)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = _bash(
        'source "$1"; TEMP_ROOT="$2"; cleanup_stale_resources',
        str(BACKUP),
        str(tmp_path),
        env=env,
    )
    assert result.returncode != 0
    assert workdir.exists(), "cleanup must stop when Docker cannot enumerate owned containers"


def test_stale_resource_cleanup_does_not_remove_workspace_when_container_removal_fails(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = ps ]; then printf '%s\\n' 0123456789ab; exit 0; fi\n"
        "exit 43\n"
    )
    docker.chmod(0o755)
    workdir = tmp_path / "maxibot-bunny.must-stay"
    workdir.mkdir(mode=0o700)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = _bash(
        'source "$1"; TEMP_ROOT="$2"; cleanup_stale_resources',
        str(BACKUP),
        str(tmp_path),
        env=env,
    )
    assert result.returncode != 0
    assert workdir.exists()
    assert "cannot remove a stale Bunny backup container" in result.stderr


def test_stale_resource_cleanup_verifies_container_is_gone_before_deleting_workspace(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = ps ]; then printf '%s\\n' 0123456789ab; exit 0; fi\n"
        "if [ \"$1\" = rm ]; then exit 0; fi\n"
        "exit 90\n"
    )
    docker.chmod(0o755)
    workdir = tmp_path / "maxibot-bunny.must-stay"
    workdir.mkdir(mode=0o700)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = _bash(
        'source "$1"; TEMP_ROOT="$2"; cleanup_stale_resources',
        str(BACKUP),
        str(tmp_path),
        env=env,
    )
    assert result.returncode != 0
    assert workdir.exists()
    assert "container remains after cleanup" in result.stderr


def test_backup_and_restore_success_is_logged_only_after_retention():
    source = BACKUP.read_text()
    assert source.index("prune_expired_backups || fail \"Bunny retention step failed") < source.index(
        'log "PASS: encrypted Bunny backup uploaded and verified'
    )
    assert source.index("prune_expired_backups || fail \"Bunny retention cleanup failed") < source.index(
        'log "PASS: isolated Bunny S3 PostgreSQL and uploads restore'
    )
    assert source.index('[[ "$restored_uploads_bytes" == "$uploads_bytes" ]]') < source.index(
        'publish_restore_marker "$latest_key" "$expected_sha"'
    )


def test_next_run_cleanup_precedes_credentials_and_never_touches_production_data():
    source = BACKUP.read_text()
    main = source[source.index("main() {"):]
    assert main.index("cleanup_stale_resources") < main.index("validate_config")
    cleanup = source[source.index("cleanup_stale_resources() {"):source.index("cleanup() {")]
    assert "docker volume" not in cleanup
    assert "docker system prune" not in cleanup
    assert "aws_s3" not in cleanup
    assert "docker exec" not in cleanup
    run_lines = [line for line in source.splitlines() if "docker run" in line]
    assert len(run_lines) == 3
    assert all("--label maxibot.bunny-backup=managed" in line for line in run_lines)
