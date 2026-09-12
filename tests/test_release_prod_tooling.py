from pathlib import Path
import subprocess
import os
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_prod.sh"
CURRENT_HEAD_MIGRATION = "0089_creative_experiment_v1"


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
        "PRE_APPLY_IMAGE_PARITY PASS",
        "PRE_APPLY_API_IMAGE",
        "PRE_APPLY_WORKER_IMAGE",
        "refs/maxibot/release/",
        "git push origin \"$TARGET_SHA:$RELEASE_REF\"",
        "REMOTE_RELEASE_REF_SHA",
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


def test_release_context_rewrites_the_current_image_and_commit_marker():
    source = SCRIPT.read_text()
    compose_rewriter = source[source.index('python3 - "$WORK/docker-compose.vps.yml"'):source.index('cat > "$WORK/release-manifest.json"')]
    assert "import re" in compose_rewriter
    assert 're.sub(r"maxibot-vps-api:[^\\s\\\"]+"' in compose_rewriter
    for marker in ("MAXIBOT_RELEASE_ID", "MAXIBOT_RELEASE_MARKER", "MAXIBOT_COMMIT_SHA"):
        assert f"re.sub(r'{marker}" in compose_rewriter


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
    assert "TARGET_DOWN_REVISION=0088_durable_ads_objective_input" in result.stdout


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


def test_release_parity_gate_precedes_migration_and_release_ref_is_durable():
    source = SCRIPT.read_text()
    assert source.index("PRE_APPLY_IMAGE_PARITY PASS") < source.index("alembic upgrade $TARGET_MIGRATION")
    assert source.index('RELEASE_REF="refs/maxibot/release/') < source.index("say RELEASE_STATUS PASS")
    assert "git ls-remote --refs origin \"$RELEASE_REF\"" in source


def _migration_graph_checker():
    source = SCRIPT.read_text()
    match = re.search(
        r'MIGRATION_PATH=\$\(python3 - "\$TARGET_SHA" "\$CURRENT_PRODUCTION_DB_HEAD" "\$TARGET_MIGRATION" <<\'PY\'\n(.*?)\nPY\n\)',
        source,
        re.S,
    )
    assert match, "migration graph checker is missing"
    return match.group(1)


def _run_graph_checker(files, current, target):
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory)
        (repo / "alembic/versions").mkdir(parents=True)
        for name, content in files.items():
            path = repo / "alembic/versions" / name
            path.write_text(content)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Release tests"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "graph"], cwd=repo, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        return subprocess.run(
            [sys.executable, "-", sha, current, target],
            cwd=repo,
            input=_migration_graph_checker(),
            text=True,
            capture_output=True,
        )


def _revision(name, parent):
    return f'revision = "{name}"\ndown_revision = {parent!r}\n'


def test_migration_graph_allows_unique_linear_upgrade_from_0085_to_0087():
    result = _run_graph_checker(
        {
            "0085.py": _revision("0085", None),
            "0086.py": _revision("0086", "0085"),
            "0087.py": _revision("0087", "0086"),
        },
        "0085",
        "0087",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0085->0086->0087"


def test_migration_graph_denies_unrelated_branch_and_absent_current():
    files = {
        "0085.py": _revision("0085", None),
        "0086.py": _revision("0086", "0085"),
        "0090.py": _revision("0090", "0085"),
        "0087.py": _revision("0087", "0086"),
    }
    unrelated = _run_graph_checker(files, "0090", "0087")
    absent = _run_graph_checker(files, "0000", "0087")
    assert unrelated.returncode != 0
    assert "no forward migration path" in unrelated.stderr
    assert absent.returncode != 0
    assert "absent from candidate graph" in absent.stderr


def test_migration_graph_denies_ambiguous_diverged_path():
    result = _run_graph_checker(
        {
            "0085.py": _revision("0085", None),
            "0086a.py": _revision("0086a", "0085"),
            "0086b.py": _revision("0086b", "0085"),
            "0087.py": _revision("0087", ("0086a", "0086b")),
        },
        "0085",
        "0087",
    )
    assert result.returncode != 0
    assert "ambiguous migration path" in result.stderr
