from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/postgres_migration_rehearsal.py").read_text()
MIGRATION = (ROOT / "alembic/versions/0099_metrica_upload_reconciliation_v1.py").read_text()


def test_postgres_rehearsal_requires_real_postgresql_and_preserves_queue_rows():
    assert 'connection.dialect.name != "postgresql"' in SCRIPT
    assert 'alembic(BASELINE)' in SCRIPT and 'alembic(TARGET)' in SCRIPT
    assert 'before_digest != after_digest' in SCRIPT
    assert 'len(after_rows) != REHEARSAL_ROWS' in SCRIPT
    assert 'version != TARGET' in SCRIPT
    assert 'POSTGRES_MIGRATION_REHEARSAL=PASS' in SCRIPT
    assert "REHEARSAL_ROWS = 1000" in SCRIPT
    assert "TrafficMembership" in SCRIPT and "TrafficVisit" in SCRIPT
    assert "session.add_all([admin, channel])" in SCRIPT
    assert SCRIPT.index("session.add_all([admin, channel])") < SCRIPT.index("session.add(landing)")
    assert "session_replication_role" not in SCRIPT
    assert "BASELINE_ORM_COMPATIBILITY=PASS" in SCRIPT
    assert "BASELINE_AND_CANDIDATE_ORM=PASS" in SCRIPT
    assert "before_digest != after_digest" in SCRIPT


def test_0099_uses_bounded_lock_wait_concurrent_index_and_disables_destructive_downgrade():
    assert "SET LOCAL lock_timeout = '5s'" in MIGRATION
    assert "postgresql_concurrently=True" in MIGRATION
    assert "index.indisvalid and index.indisready" in MIGRATION
    assert 'names != ["status", "metrica_upload_next_check_at"]' in MIGRATION
    assert "0099 downgrade is disabled" in MIGRATION
    assert "op.drop_column" not in MIGRATION
