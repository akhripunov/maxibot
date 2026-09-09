from pathlib import Path
import subprocess
import os


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_prod.sh"
CURRENT_HEAD_MIGRATION = "0081_ads_bot_ai_support_v1"


def test_release_entrypoint_is_valid_shell():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_dry_run_is_non_mutating_and_fail_closed():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env = os.environ.copy()
    env["TARGET_MIGRATION"] = CURRENT_HEAD_MIGRATION
    result = subprocess.run(
        [str(SCRIPT), sha, "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert "MIGRATION_ALLOWED=NO" in result.stdout
    assert "MIGRATION_BEFORE_SWITCH_READY_BLOCKED=YES" in result.stdout
    assert "DRY_RUN_RESULT=PASS" in result.stdout


def test_release_tooling_contains_required_safety_gates():
    source = SCRIPT.read_text()
    for marker in (
        "OWNER_APPROVED=1",
        "env_file: /opt/maxibot/.env",
        "--platform linux/amd64",
        "MIGRATION_ALLOWED YES",
        "alembic upgrade $TARGET_MIGRATION",
        "health/ready",
        'MAX_RUN_POLLING: "false"',
        'MAX_RUN_POLLING: "true"',
        "rollback-compose.vps.yml",
        "release-report.env",
        "HEALTH_TIMEOUT_SECONDS",
        "HEALTH_POLL_INTERVAL_SECONDS",
        "CANONICAL_PRODUCTION_HOST=135.106.166.46",
        "PRODUCTION_SSH_USER=root",
        'PRODUCTION_TARGET="$PRODUCTION_SSH_USER@$PRODUCTION_HOST"',
        '"${SSH[@]}" true',
        "SSH_PREFLIGHT PASS",
        "docker inspect --format '{{.State.Status}}'",
        "RUNTIME_HEALTH=FAIL_CONTAINER_EXITED",
        "RUNTIME_HEALTH=FAIL_TIMEOUT",
        "sleep $HEALTH_POLL_INTERVAL_SECONDS",
    ):
        assert marker in source


def test_readiness_probe_is_bounded_and_exit_first():
    source = SCRIPT.read_text()
    assert "deadline=\\$((\\$(date +%s) + $HEALTH_TIMEOUT_SECONDS))" in source
    assert '[[ \\"\\$state\\" != running ]]' in source
    assert "if docker exec maxibot-vps-api-1 python" in source
    assert "if [[ \\$(date +%s) -ge \\$deadline ]]" in source
    assert source.index("RUNTIME_HEALTH=FAIL_CONTAINER_EXITED") < source.index("sleep $HEALTH_POLL_INTERVAL_SECONDS")
    assert source.index("RUNTIME_HEALTH=FAIL_TIMEOUT") < source.index("POSTDEPLOY_HEALTH=FAIL")


def test_exact_sha_is_proven_without_git_inside_release_directory():
    source = SCRIPT.read_text()
    assert "org.maxibot.release.sha" in source
    assert '{{index .Config.Labels \\"org.maxibot.release.sha\\"}}' in source
    assert '.Config.Labels.org.maxibot.release.sha' not in source
    assert "release-manifest.json" in source


def test_production_ssh_target_is_explicit_and_canonical():
    source = SCRIPT.read_text()
    assert "PRODUCTION_SSH_USER=${USER" not in source
    assert "PRODUCTION_SSH_USER=root" in source
    assert 'PRODUCTION_TARGET="$PRODUCTION_SSH_USER@$PRODUCTION_HOST"' in source
    assert '"${SCP[@]}" "$ARCHIVE" "$PRODUCTION_TARGET:$REMOTE_TMP"' in source
    assert '[[ "$PRODUCTION_HOST" == "$CANONICAL_PRODUCTION_HOST" ]]' in source
    assert 'if ! "${SSH[@]}" true; then' in source
    assert "SSH_PREFLIGHT PASS" in source


def test_frozen_guard_exempts_only_ads_schema_plumbing():
    source = (ROOT / "scripts/frozen_path_guard.sh").read_text()
    assert "def ads_only_shared_change(path: str)" in source
    assert '"AdsPromotionCampaignLink"' in source
    assert '"ads_recommendations"' in source
    assert '"ads_ai_outcomes"' in source
    assert "shared_for_impact" in source
    assert 'print("FROZEN_PATH_GUARD=BLOCK")' in source
    assert "def ads_only_channel_ui_change(path: str)" in source
    assert "app/bot/menu.py" in source


def test_release_context_excludes_appledouble_and_keeps_alembic_preflight():
    source = SCRIPT.read_text()
    assert "find \"$WORK\" \\( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \\) -exec rm -rf {} +" in source
    assert "tar --exclude='._*' --exclude='.DS_Store' --exclude='__MACOSX'" in source
    assert 'AppleDouble or macOS metadata remains in release context' in source
    archive_line = source.index("tar --exclude='._*'")
    preflight_line = source.index("docker run --rm --network maxibot-vps-internal", archive_line)
    assert archive_line < preflight_line
    assert "alembic heads" in source


def test_release_tooling_has_width_bridge_and_backup_gate():
    bridge = (ROOT / "alembic/versions/0075a_alembic_version_width.py").read_text()
    source = SCRIPT.read_text()
    assert 'revision = "0075a_alembic_version_width"' in bridge
    assert 'type_=sa.String(length=64)' in bridge
    assert "pg_dump" in source
    assert "pg_restore --list" in source
    assert "BACKUP_VALIDATION PASS" in source
    assert "BACKUP_DISK_PREFLIGHT PASS" in source


def _run_release(target_migration=None):
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env = os.environ.copy()
    if target_migration is None:
        env.pop("TARGET_MIGRATION", None)
    else:
        env["TARGET_MIGRATION"] = target_migration
    return subprocess.run(
        [str(SCRIPT), sha, "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_release_parser_handles_real_merge_tuple_and_current_head_path():
    source = SCRIPT.read_text()
    assert "isinstance(value, (tuple, list))" in source
    assert 'down_revision = ("0059_ads_ai_worker_release", "0058_metrica_upload_tracking")' in (
        ROOT / "alembic/versions/0060_merge_ads_foundation_heads.py"
    ).read_text()
    result = _run_release(CURRENT_HEAD_MIGRATION)
    assert result.returncode == 0, result.stderr
    assert f"TARGET_MIGRATION={CURRENT_HEAD_MIGRATION}" in result.stdout
    assert "TARGET_DOWN_REVISION=0080_creative_storage_intelligence_v1" in result.stdout


def test_release_parser_rejects_invalid_omitted_and_unreachable_targets():
    invalid = _run_release("does-not-exist")
    assert invalid.returncode != 0
    assert "requested Alembic migration is absent" in invalid.stderr

    unreachable = _run_release("0071_bot_paid_dedupe")
    assert unreachable.returncode != 0
    assert "not a reachable head" in unreachable.stderr

    omitted = _run_release()
    assert omitted.returncode != 0
    assert "multiple Alembic heads" not in omitted.stderr
    assert "TARGET_MIGRATION" in omitted.stderr


def test_dry_run_does_not_run_database_mutation():
    result = _run_release(CURRENT_HEAD_MIGRATION)
    assert result.returncode == 0, result.stderr
    assert "MIGRATION_ALLOWED=NO" in result.stdout
    assert "alembic upgrade" not in result.stdout
