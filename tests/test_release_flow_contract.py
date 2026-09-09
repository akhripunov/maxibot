from pathlib import Path
import subprocess
import os


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release_prod.sh"
QA = ROOT / "scripts" / "qa_check.sh"


def test_release_flow_has_distinct_fail_closed_candidate_and_release_gates():
    source = QA.read_text(encoding="utf-8")
    assert "compute_release_gates()" in source
    assert 'CANDIDATE_READY="NO"' in source
    assert 'RELEASE_READY="NO"' in source
    assert 'CANDIDATE_READY="YES"' in source
    assert 'RELEASE_READY="YES"' in source
    assert 'if [ "$CANDIDATE_READY" != YES ]; then' in source
    assert 'if [ "$RELEASE_READY" != YES ]; then' in source
    assert "CURRENT_PRODUCTION_DB_HEAD" in source
    assert 'QA_MIGRATION_BASELINE:-UNKNOWN' in source


def test_release_ready_requires_rehearsal_access_backup_rollback_and_parity():
    source = QA.read_text(encoding="utf-8")
    for marker in (
        "QA_MIGRATION_REHEARSAL",
        "QA_DOCKER_BUILD",
        "QA_COMPOSE_RENDER",
        "QA_IMAGE_PARITY",
        "QA_ACCESS_PREFLIGHT",
        "QA_BACKUP_FEASIBILITY",
        "QA_ROLLBACK_FEASIBILITY",
        "QA_WORKER_SYNC",
        "QA_MAC_PARITY",
    ):
        assert marker in source


def test_apply_requires_exact_candidate_approval_and_resume_sha():
    source = RELEASE.read_text(encoding="utf-8")
    assert 'CANDIDATE_READY:-' in source
    assert 'RELEASE_READY:-' in source
    assert 'RELEASE_APPROVED_SHA:-' in source
    assert 'RESUME_RELEASE_SHA:-' in source
    assert "say RESUME_SAME_RELEASE PASS" in source


def test_baseline_and_image_parity_are_before_migration():
    source = RELEASE.read_text(encoding="utf-8")
    baseline = source.index("CURRENT_PRODUCTION_DB_HEAD=$(remote")
    archive = source.index("git archive")
    image_gate = source.index("say IMAGE_REFERENCE_GATE PASS")
    migration = source.index("alembic upgrade $TARGET_MIGRATION")
    assert baseline < archive
    assert image_gate < migration
    assert "docker compose -p maxibot-vps -f docker-compose.vps.yml config --images" in source
    assert "image_count != 2" in source


def test_mismatched_owner_approval_fails_before_ssh_or_migration():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    env = os.environ.copy()
    env["TARGET_MIGRATION"] = "0077_ads_storage_lifecycle_v1"
    env["CANDIDATE_READY"] = "YES"
    env["RELEASE_READY"] = "YES"
    env["RELEASE_APPROVED_SHA"] = "0" * 40
    env["OWNER_APPROVED"] = "1"
    result = subprocess.run([str(RELEASE), sha, "--apply"], cwd=ROOT, env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "RELEASE_APPROVED_SHA must equal target SHA" in result.stderr
    assert "SSH_PREFLIGHT" not in result.stdout
