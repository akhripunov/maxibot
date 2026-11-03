from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "alembic/versions/0075a_alembic_version_width.py"
RELEASE = ROOT / "scripts/release_prod.sh"


def test_version_width_bridge_is_short_and_precedes_long_revision():
    bridge = BRIDGE.read_text(encoding="utf-8")
    revision = re.search(r'^revision\s*=\s*"([^"]+)"', bridge, re.M).group(1)
    assert len(revision) <= 32
    assert 'down_revision = "0075_ads_canonical_v2"' in bridge
    assert 'type_=sa.String(length=64)' in bridge
    assert '"alembic_version"' in bridge
    assert '"version_num"' in bridge
    assert 'down_revision = "0075a_alembic_version_width"' in (
        ROOT / "alembic/versions/0076_ads_directologist_decision_engine_v1.py"
    ).read_text(encoding="utf-8")


def test_release_script_requires_validated_backup_before_migration():
    source = RELEASE.read_text(encoding="utf-8")
    backup = source.index("BACKUP_PATH=$(remote")
    migration = source.index("alembic upgrade $TARGET_MIGRATION")
    assert backup < migration
    for marker in (
        "pg_dump",
        "--format=custom",
        "test -s \\\"\\$backup_path\\\"",
        "pg_restore --list",
        "BACKUP_VALIDATION PASS",
        "BACKUP_DISK_PREFLIGHT PASS",
        "ROLLBACK_READY YES",
        "MIGRATION_ALLOWED YES",
    ):
        assert marker in source


def test_release_script_keeps_migration_and_runtime_switch_order():
    source = RELEASE.read_text(encoding="utf-8")
    migration = source.index("alembic upgrade $TARGET_MIGRATION")
    switch = source.index("docker compose --env-file $PRODUCTION_ROOT/.env -p maxibot-vps -f $PRODUCTION_ROOT/releases/$TARGET_SHA/docker-compose.vps.yml up -d")
    assert migration < switch
