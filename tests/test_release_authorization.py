import sys
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from release_authorization import (AuthorizationError, candidate_scope_diff,
                                   validate_task_authorization,
                                   assert_task_scoped_no_migration)  # noqa: E402


AUTH_ID = "maxibot-task-2026-10-06-001"
BASELINE = "a" * 40
CANDIDATE = "b" * 40


def record(**overrides):
    value = {
        "schema_version": 3,
        "owner_instruction_scope": "FULL_PRODUCTION_CYCLE",
        "project": "maxibot",
        "authorization_mode": "TASK_SCOPED_AUTO",
        "authorization_id": AUTH_ID,
        "source_reference": "linear:release-orchestrator-handoff#owner-instruction-2026-10-06",
        "production_baseline_sha": BASELINE,
        "task": "Change one user-facing menu link",
        "allowed_paths": ["app/bot/menu.py"],
        "acceptance_criteria": ["menu opens the configured website"],
        "exclusions": ["database", "migrations", "security", "secrets", "payments",
                       "ads", "paid_traffic", "release_infrastructure"],
        "state": "ACTIVE",
        "revoked": False,
        "consumed": False,
    }
    value.update(overrides)
    return value


def validate(**overrides):
    params = {
        "record": record(), "project": "maxibot", "authorization_id": AUTH_ID,
        "candidate_sha": CANDIDATE, "current_main_sha": CANDIDATE,
        "production_sha": BASELINE, "changed_paths": ["app/bot/menu.py"],
    }
    params.update(overrides)
    return validate_task_authorization(**params)


def test_task_authorization_accepts_exact_candidate_baseline_and_scoped_paths():
    result = validate()
    assert result["authorization_id"] == AUTH_ID
    assert result["candidate_sha"] == CANDIDATE
    assert result["production_baseline_sha"] == BASELINE


def test_task_authorization_requires_explicit_full_cycle_owner_intent():
    with pytest.raises(AuthorizationError, match="explicitly authorize the full production cycle"):
        validate(record=record(owner_instruction_scope="PREPARE_ONLY"))
    with pytest.raises(AuthorizationError, match="Unsupported task authorization record"):
        validate(record=record(schema_version=1))


@pytest.mark.parametrize("path", [
    ".github/workflows/maxibot-deploy.yml", "scripts/release_prod.sh",
    "alembic/versions/0099_change.py", "app/ads/decision.py",
    "app/integrations/yandex_direct.py", "app/security/auth.py", "payments/checkout.py",
])
def test_task_authorization_rejects_owner_only_paths_even_if_allowlisted(path):
    with pytest.raises(AuthorizationError, match="owner-only"):
        validate(record=record(allowed_paths=[path]), changed_paths=[path])


def test_task_authorization_rejects_unrelated_path():
    with pytest.raises(AuthorizationError, match="outside authorized scope"):
        validate(changed_paths=["app/bot/menu.py", "docs/README.md"])


def test_task_authorization_rejects_candidate_or_production_baseline_drift():
    with pytest.raises(AuthorizationError, match="exact current main"):
        validate(current_main_sha="c" * 40)
    with pytest.raises(AuthorizationError, match="production baseline"):
        validate(production_sha="d" * 40)


@pytest.mark.parametrize("field,value", [
    ("source_reference", ""), ("acceptance_criteria", []), ("allowed_paths", []),
    ("exclusions", ["database"]), ("state", "REVOKED"), ("revoked", True),
    ("consumed", True),
])
def test_task_authorization_fails_closed_for_incomplete_revoked_or_consumed_record(field, value):
    with pytest.raises(AuthorizationError):
        validate(record=record(**{field: value}))


def test_task_authorization_rejects_wrong_project_or_authorization_id():
    with pytest.raises(AuthorizationError, match="project"):
        validate(project="gpoint")
    with pytest.raises(AuthorizationError, match="authorization ID"):
        validate(authorization_id="another-authorization")


def test_task_scoped_auto_cannot_change_or_reconcile_database_migration_head():
    assert_task_scoped_no_migration("head_0098", {"PROD_DB_HEAD": "head_0098"})
    with pytest.raises(AuthorizationError, match="cannot apply database migrations"):
        assert_task_scoped_no_migration("head_0098", {"PROD_DB_HEAD": "head_0097"})


def test_task_authorization_allows_only_a_complete_preapproved_migration_contract():
    migration = "alembic/versions/0099_safe_change.py"
    contract = {"allowed_paths": [migration], "target_migration": "0099_safe_change",
                "production_db_head": "0098_old", "risk_description": "Add one nullable indexed field.",
                "backup_plan": "Create and verify the canonical database backup before migration.",
                "rollback_plan": "Restore the verified backup or stop for forward repair.",
                "stop_conditions": ["Stop if baseline differs.", "Stop if backup validation fails."],
                "required_gates": ["migration_rehearsal", "backup_before_migration",
                                   "rollback_readiness", "postdeploy_health"]}
    proof = validate(record=record(allowed_paths=[migration], risk_contracts={"database_migration": contract}),
                     changed_paths=[migration])
    assert proof["changed_paths"] == migration
    with pytest.raises(AuthorizationError, match="complete pre-approved database risk contract"):
        validate(record=record(allowed_paths=[migration]), changed_paths=[migration])
    with pytest.raises(AuthorizationError, match="outside the durable authorized scope"):
        validate(record=record(allowed_paths=[migration], risk_contracts={"database_migration": contract}),
                 changed_paths=[migration, "alembic/versions/0100_extra.py"])


def test_task_scoped_migration_requires_target_baseline_rehearsal_and_rollback_evidence():
    contract = {"target_migration": "0099_safe_change", "production_db_head": "0098_old",
                "risk_description": "Add one nullable indexed field.",
                "backup_plan": "Create and verify the canonical database backup before migration.",
                "rollback_plan": "Restore the verified backup or stop for forward repair.",
                "stop_conditions": ["Stop if baseline differs.", "Stop if backup validation fails."],
                "required_gates": ["migration_rehearsal", "backup_before_migration",
                                   "rollback_readiness", "postdeploy_health"],
                "allowed_paths": ["alembic/versions/0099_safe_change.py"]}
    evidence = {"PROD_DB_HEAD": "0098_old", "MIGRATION_REHEARSAL": "PASS PATH=0098_old->0099_safe_change",
                "ROLLBACK_READY": "PASS", "PROD_READY": "ready"}
    assert_task_scoped_no_migration("0099_safe_change", evidence,
                                    record=record(risk_contracts={"database_migration": contract}),
                                    changed_paths=contract["allowed_paths"])
    for change in ({"PROD_DB_HEAD": "0097_old"}, {"MIGRATION_REHEARSAL": "BLOCKED"},
                   {"ROLLBACK_READY": "METADATA_REQUIRED"}):
        with pytest.raises(AuthorizationError):
            assert_task_scoped_no_migration("0099_safe_change", {**evidence, **change},
                                            record=record(risk_contracts={"database_migration": contract}),
                                            changed_paths=contract["allowed_paths"])


def test_authorization_claim_marker_is_project_scoped_and_one_shot():
    from release_authorization import authorization_state_ref
    assert authorization_state_ref("maxibot", AUTH_ID, "claimed") == f"refs/tags/maxibot-task-auth/{AUTH_ID}/claimed"
    with pytest.raises(AuthorizationError):
        authorization_state_ref("gpoint", AUTH_ID, "claimed")


def make_git_repo(root: Path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Release test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "release-test@example.invalid"], cwd=root, check=True)
    (root / "README.md").write_text("baseline\n")
    git("add", "README.md")
    git("commit", "-m", "baseline")
    baseline = git("rev-parse", "HEAD")
    return git, baseline


def test_scope_diff_requires_authorization_record_commit_before_task_changes(tmp_path):
    git, baseline = make_git_repo(tmp_path)
    auth_path = tmp_path / ".release-authorizations" / f"{AUTH_ID}.json"
    auth_path.parent.mkdir()
    auth_path.write_text("{}\n")
    git("add", ".release-authorizations")
    git("commit", "-m", "record task authorization")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "menu.py").write_text("updated\n")
    git("add", "app/menu.py")
    git("commit", "-m", "implement authorized task")
    candidate = git("rev-parse", "HEAD")
    assert candidate_scope_diff(tmp_path, AUTH_ID, baseline, candidate) == ["app/menu.py"]


def test_scope_diff_rejects_authorization_record_added_with_or_after_code(tmp_path):
    git, baseline = make_git_repo(tmp_path)
    auth_path = tmp_path / ".release-authorizations" / f"{AUTH_ID}.json"
    auth_path.parent.mkdir()
    auth_path.write_text("{}\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "menu.py").write_text("updated\n")
    git("add", ".release-authorizations", "app/menu.py")
    git("commit", "-m", "implement and authorize together")
    candidate = git("rev-parse", "HEAD")
    with pytest.raises(AuthorizationError, match="precede every implementation change"):
        candidate_scope_diff(tmp_path, AUTH_ID, baseline, candidate)


def test_scope_diff_accepts_non_squashed_merge_with_record_committed_before_code(tmp_path):
    git, baseline = make_git_repo(tmp_path)
    git("branch", "base", baseline)
    git("switch", "-c", "authorized-task")
    auth_path = tmp_path / ".release-authorizations" / f"{AUTH_ID}.json"
    auth_path.parent.mkdir()
    auth_path.write_text("{}\n")
    git("add", ".release-authorizations")
    git("commit", "-m", "record task authorization")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "menu.py").write_text("updated\n")
    git("add", "app/menu.py")
    git("commit", "-m", "implement authorized task")
    git("switch", "base")
    git("merge", "--no-ff", "authorized-task", "-m", "merge authorized task")
    candidate = git("rev-parse", "HEAD")
    assert candidate_scope_diff(tmp_path, AUTH_ID, baseline, candidate) == ["app/menu.py"]


def test_scope_diff_rejects_squash_merge_that_erases_record_order(tmp_path):
    git, baseline = make_git_repo(tmp_path)
    git("branch", "base", baseline)
    git("switch", "-c", "authorized-task")
    auth_path = tmp_path / ".release-authorizations" / f"{AUTH_ID}.json"
    auth_path.parent.mkdir()
    auth_path.write_text("{}\n")
    git("add", ".release-authorizations")
    git("commit", "-m", "record task authorization")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "menu.py").write_text("updated\n")
    git("add", "app/menu.py")
    git("commit", "-m", "implement authorized task")
    git("switch", "base")
    git("merge", "--squash", "authorized-task")
    git("commit", "-m", "squash authorized task")
    candidate = git("rev-parse", "HEAD")
    with pytest.raises(AuthorizationError, match="precede every implementation change"):
        candidate_scope_diff(tmp_path, AUTH_ID, baseline, candidate)
