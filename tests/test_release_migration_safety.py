from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/maxibot-native-release.yml"


def test_contract_migration_gate_runs_backup_restore_apply_before_kamal():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    test_steps = workflow["jobs"]["tests"]["steps"]
    contract_index = next(i for i, step in enumerate(test_steps) if step.get("id") == "migration_contract")
    frozen_index = next(i for i, step in enumerate(test_steps) if step.get("name") == "Run exact-candidate Frozen Path Guard")
    rehearsal_index = next(i for i, step in enumerate(test_steps) if step.get("name") == "Rehearse the authorized migration on isolated PostgreSQL")
    frozen_guard = test_steps[frozen_index]
    assert contract_index < frozen_index < rehearsal_index
    assert "scripts/frozen_path_guard.sh" in frozen_guard["run"]
    assert "MIGRATION_FROZEN_PATHS" in frozen_guard.get("env", {})
    assert "--owner-approved-task" in frozen_guard["run"]
    steps = workflow["jobs"]["deploy"]["steps"]
    baseline = next(step for step in steps if step.get("id") == "baseline")["run"]
    assert "validate_owner_main_publication" in baseline
    assert "MIGRATION_OWNER_MAIN_PUBLICATION=PASS" in baseline
    names = [step.get("name", "") for step in steps]
    backup = next(step for step in steps if step.get("name") == "Create and restore-check a fresh database backup for the approved migration")
    migration = next(step for step in steps if step.get("name") == "Apply or forward-resume the exact contract target from the immutable candidate image")
    runtime = next(step for step in steps if step.get("name") == "Verify runtime, network connectivity, and Worker heartbeat")

    assert "migration_release_contract.py" in baseline
    assert "MIGRATION_RISK_CONTRACT=PASS" in baseline
    assert "source_revision" in baseline and "target_revision" in baseline
    assert "preserved_tables" in baseline
    assert "pg_dump -Fc" in backup["run"]
    assert "pg_restore --list" in backup["run"]
    assert "--network none" in backup["run"]
    assert "cleanup" in backup["run"]
    assert "source_preserved_counts" in backup["run"] and "restored_preserved_counts" in backup["run"]
    assert 'alembic upgrade "$migration_target"' in migration["run"]
    assert migration.get("env", {}).get("MIGRATION_RESUME") == "${{ steps.baseline.outputs.migration_resume }}"
    assert 'if [[ "$migration_resume" == false ]]; then' in migration["run"]
    assert '[[ "$expected_db_head" == "$migration_target" ]]' in migration["run"]
    assert "BACKUP_PRESERVED_COUNTS" in migration.get("env", {})
    assert "queue_rows_non_decreasing" in migration["run"]
    assert names.index(backup["name"]) < names.index(migration["name"]) < names.index("Record exact production Deployment after all preflight gates")
    assert runtime.get("env", {}).get("EXPECTED_DATABASE_HEAD") == "${{ steps.baseline.outputs.expected_database_head }}"
    assert "db_head == expected_db_head" in runtime["run"]
