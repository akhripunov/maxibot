import os
import subprocess
import hashlib
from pathlib import Path


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
    ]
    for inspect_state, remove_status, expected in scenarios:
        env = os.environ | {
            "MOCK_INSPECT_STATE": inspect_state,
            "MOCK_REMOVE_STATUS": remove_status,
            "TMPDIR": str(tmp_path),
        }
        result = _bash(
            'source "$1"; '
            'docker() { '
            '  if [[ "$1" == inspect ]]; then '
            '    case "$MOCK_INSPECT_STATE" in '
            '      present) return 0 ;; '
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

    assert "OnCalendar=*-*-01 08:00:00 UTC" in timer
    assert "Persistent=true" in timer


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
