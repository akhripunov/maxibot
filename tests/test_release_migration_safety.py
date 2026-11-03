from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/maxibot-native-release.yml"


def _migration_steps():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    steps = workflow["jobs"]["deploy"]["steps"]
    backup = next(step for step in steps if step.get("id") == "migration_backup")
    migration = next(step for step in steps if step.get("id") == "migration_apply")
    return steps, backup, migration


def test_native_migration_backup_uses_one_snapshot_and_isolated_verified_restore():
    steps, backup, migration = _migration_steps()
    script = backup["run"]

    assert backup["if"] == "steps.baseline.outputs.migration_required == 'true'"
    assert steps.index(backup) < steps.index(migration)
    for marker in (
        "snapshot_begin",
        "pg_export_snapshot()",
        "pg_dump -Fc --snapshot=",
        "source_queue_sha",
        "source_preserved_counts",
        "snapshot_query",
        "pg_restore --list",
        "--network none",
        "restored_queue_sha",
        "restored_preserved_counts",
        "MIGRATION_RESTORE_COMPARE=PASS",
        "isolated migration restore cleanup failed",
    ):
        assert marker in script

    assert script.index("source_preserved_counts=") < script.index("pg_dump -Fc --snapshot=")
    assert 'source_preserved_counts="$(snapshot_preserved_counts)"' in script


def test_native_migration_apply_is_forward_only_and_follows_verified_backup():
    steps, backup, migration = _migration_steps()
    apply_script = migration["run"]

    assert steps.index(backup) < steps.index(migration)
    assert 'alembic upgrade "$migration_target"' in apply_script
    assert "alembic downgrade" not in apply_script
    assert '[[ "$expected_db_head" == "$migration_target" ]]' in apply_script
    assert "MIGRATION_APPLY=SUCCESS" in apply_script
