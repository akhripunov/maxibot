from pathlib import Path
import subprocess
import os
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_prod.sh"
PRODUCTION_HEAD_MIGRATION = "0090_self_learning_loop_v2"
CANDIDATE_HEAD_MIGRATION = "0098_durable_autopilot_execution_claim_v1"
CANDIDATE_PARENT_MIGRATION = "0097_experiment_budget_cap_v1"
IMAGE_PREFLIGHT = ROOT / "scripts" / "release_image_preflight.py"
APPROVED_SHA = "a" * 40


def _run_image_preflight(tmp_path, api="good", worker="good", platform="linux/amd64"):
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then\n"
        "  image=\"$3\"\n"
        "  case \"$image\" in\n"
        f"    *api*) value={api!r};;\n"
        f"    *worker*) value={worker!r};;\n"
        "    *) value=missing;;\n"
        "  esac\n"
        "  case \"$value\" in\n"
        f"    good) printf '%s|x|{APPROVED_SHA}|\\n' '" + platform + "' ;;\n"
        "    stale) printf '%s|x|%s|\\n' 'linux/amd64' 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' ;;\n"
        "    wrong_platform) printf '%s|x|%s|\\n' 'linux/arm64' '" + APPROVED_SHA + "' ;;\n"
        "    missing) exit 1;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\nexit 1\n"
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    return subprocess.run(
        [sys.executable, str(IMAGE_PREFLIGHT), APPROVED_SHA, "candidate-api", "candidate-worker"],
        capture_output=True,
        text=True,
        env=env,
    )


def test_prebuilt_exact_api_worker_images_are_ready(tmp_path):
    result = _run_image_preflight(tmp_path)
    assert result.returncode == 0
    assert "IMAGE_READY=YES" in result.stdout
    assert f"API_IMAGE_SHA={APPROVED_SHA}" in result.stdout
    assert f"WORKER_IMAGE_SHA={APPROVED_SHA}" in result.stdout


def test_prebuilt_image_missing_is_denied(tmp_path):
    result = _run_image_preflight(tmp_path, api="missing")
    assert result.returncode != 0
    assert "image_inspect_failed" in result.stderr


def test_prebuilt_stale_image_sha_is_denied(tmp_path):
    result = _run_image_preflight(tmp_path, api="stale")
    assert result.returncode != 0
    assert "source_sha_mismatch" in result.stderr


def test_prebuilt_api_worker_mismatch_is_denied(tmp_path):
    result = _run_image_preflight(tmp_path, worker="stale")
    assert result.returncode != 0
    assert "source_sha_mismatch" in result.stderr


def test_prebuilt_wrong_architecture_is_denied(tmp_path):
    result = _run_image_preflight(tmp_path, platform="linux/arm64")
    assert result.returncode != 0
    assert "wrong_platform" in result.stderr


def test_release_dry_run_has_prebuilt_image_gate():
    source = SCRIPT.read_text()
    assert "release_image_preflight.py" in source
    assert "API_WORKER_IMAGE_PARITY PASS" in source
    assert "RELEASE_READY YES" in source
    assert "docker compose" not in source[source.index("if [[ \"$MODE\" == DRY_RUN ]]; then"):source.index("fi\n\n[[ \"${OWNER_APPROVED")]


def test_production_dry_run_uses_one_ssh_session_and_names_failed_checks():
    source = SCRIPT.read_text()
    dry_run = source[source.index('if [[ "$MODE" == DRY_RUN ]]; then'):source.index('fi\n\n[[ "${OWNER_APPROVED')]
    assert '"${PREFLIGHT_SSH[@]}" \'bash -s\'' in dry_run
    assert "REMOTE_PREFLIGHT" in dry_run
    assert "SSH_REMOTE_PREFLIGHT PASS" in dry_run
    assert "SSH_REMOTE_PREFLIGHT FAIL" in dry_run
    assert "PRODUCTION_PREFLIGHT_FAILED_CHECKS" in dry_run
    assert 'ADS_AUTOPILOT_ENABLED=false' in dry_run
    assert 'ADS_WRITE_ACTIONS_ENABLED=false' in dry_run
    assert "true >/dev/null 2>&1" not in dry_run


def test_release_dry_run_collects_production_preflight_in_one_ssh_session(tmp_path):
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = image ] && [ \"$2\" = inspect ]; then\n"
        "  printf 'linux/amd64|%s||%s\\n' \"$FAKE_IMAGE_SHA\" \"$FAKE_IMAGE_SHA\"\n"
        "elif [ \"$1\" = exec ] && [ \"$2\" = maxibot-vps-db-1 ]; then\n"
        f"  printf '%s\\n' {CANDIDATE_HEAD_MIGRATION!r}\n"
        "elif [ \"$1\" = exec ] && [ \"$2\" = maxibot-vps-api-1 ]; then\n"
        "  printf 'ready\\n'\n"
        "elif [ \"$1\" = inspect ]; then\n"
        "  case \"$*\" in\n"
        "    *'range .Config.Env'*) printf 'ADS_AUTOPILOT_ENABLED=false\\nADS_WRITE_ACTIONS_ENABLED=false\\n' ;;\n"
        "    *'project.config_files'*) printf '/opt/maxibot/releases/previous/docker-compose.vps.yml\\n' ;;\n"
        "    *) printf 'maxibot-vps-api:previous\\n' ;;\n"
        "  esac\n"
        "else\n"
        "  exit 2\n"
        "fi\n"
    )
    docker.chmod(0o755)
    ssh = tmp_path / "ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        "printf 'call\\n' >> \"$SSH_COUNTER\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in\n"
        "    -i|-o) shift 2 ;;\n"
        "    root@*) shift; break ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "[ \"$1\" = 'bash -s' ] || exit 2\n"
        "exec /bin/bash -s\n"
    )
    ssh.chmod(0o755)
    key = tmp_path / "ssh-key"
    key.write_text("fake test key")
    counter = tmp_path / "ssh-calls"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "TARGET_MIGRATION": CANDIDATE_HEAD_MIGRATION,
            "PRODUCTION_HOST": "135.106.166.46",
            "PRODUCTION_SSH_KEY": str(key),
            "FAKE_IMAGE_SHA": sha,
            "SSH_COUNTER": str(counter),
        }
    )
    result = subprocess.run(
        [str(SCRIPT), sha, "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "SSH_REMOTE_PREFLIGHT=PASS" in result.stdout, (
        result.stdout + result.stderr + (counter.read_text() if counter.exists() else "SSH_NOT_CALLED")
    )
    assert "PRODUCTION_REHEARSAL=PASS" in result.stdout
    assert "RELEASE_READY=YES" in result.stdout
    assert counter.read_text().splitlines() == ["call"]


def test_release_entrypoint_is_valid_shell():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_dry_run_is_non_mutating_and_fail_closed():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env = os.environ.copy()
    env["TARGET_MIGRATION"] = CANDIDATE_HEAD_MIGRATION
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
        "docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Os}}/{{.Architecture}}'",
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
        'ControlMaster=auto',
        'ControlPath="$SSH_CONTROL_PATH"',
        'ControlPersist=120',
        '"${SSH[@]}" true',
        "SSH_PREFLIGHT PASS",
        "docker inspect --format '{{.State.Status}}'",
        "RUNTIME_HEALTH=FAIL_CONTAINER_EXITED",
        "RUNTIME_HEALTH=FAIL_TIMEOUT",
        "sleep $HEALTH_POLL_INTERVAL_SECONDS",
        "PRE_APPLY_IMAGE_PARITY PASS",
        "PRE_APPLY_API_IMAGE",
        "PRE_APPLY_WORKER_IMAGE",
        "RELEASE_IMAGE_ARCHIVE_SHA256",
        "sha256sum $REMOTE_IMAGE_TMP",
        "VPS_LOADED_IMAGE_CONFIG_DIGEST",
        "refs/maxibot/release/",
        "git push --force-with-lease=\"$RELEASE_REF:$EXISTING_RELEASE_REF_SHA\" origin \"$TARGET_SHA:$RELEASE_REF\"",
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
    assert 'org.maxibot.release.sha' in source
    assert '.Config.Labels.org.maxibot.release.sha' not in source
    assert "release-manifest.json" in source


def test_production_ssh_target_is_explicit_and_canonical():
    source = SCRIPT.read_text()
    assert "PRODUCTION_SSH_USER=${USER" not in source
    assert "PRODUCTION_SSH_USER=root" in source
    assert 'PRODUCTION_TARGET="$PRODUCTION_SSH_USER@$PRODUCTION_HOST"' in source
    assert '"${SCP[@]}" "$ARCHIVE" "$PRODUCTION_TARGET:$REMOTE_TMP"' in source
    assert '[[ "$PRODUCTION_HOST" == "$CANONICAL_PRODUCTION_HOST" ]]' in source
    assert 'for attempt in 1 2 3; do' in source
    assert 'if "${SSH[@]}" true; then' in source
    assert 'ConnectTimeout=10' in source
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


def test_release_parser_handles_real_merge_tuple_and_candidate_head_path():
    source = SCRIPT.read_text()
    assert "isinstance(value, (tuple, list))" in source
    assert 'down_revision = ("0059_ads_ai_worker_release", "0058_metrica_upload_tracking")' in (
        ROOT / "alembic/versions/0060_merge_ads_foundation_heads.py"
    ).read_text()
    result = _run_release(CANDIDATE_HEAD_MIGRATION)
    assert result.returncode == 0, result.stderr
    assert f"TARGET_MIGRATION={CANDIDATE_HEAD_MIGRATION}" in result.stdout
    assert f"TARGET_DOWN_REVISION={CANDIDATE_PARENT_MIGRATION}" in result.stdout


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
    result = _run_release(CANDIDATE_HEAD_MIGRATION)
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


def test_migration_graph_allows_same_head_without_upgrade():
    result = _run_graph_checker(
        {
            "0091.py": _revision("0091", "0090"),
            "0090.py": _revision("0090", None),
        },
        "0091",
        "0091",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0091"


def test_migration_graph_allows_multi_step_candidate_upgrade():
    result = _run_graph_checker(
        {
            "0090.py": _revision("0090", None),
            "0091.py": _revision("0091", "0090"),
            "0092.py": _revision("0092", "0091"),
        },
        "0090",
        "0092",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0090->0091->0092"


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
