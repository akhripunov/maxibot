from pathlib import Path
from types import SimpleNamespace
import importlib.util


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/postgres_migration_rehearsal.py").read_text()
MIGRATION = (ROOT / "alembic/versions/0099_metrica_upload_reconciliation_v1.py").read_text()
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "migration_0099", ROOT / "alembic/versions/0099_metrica_upload_reconciliation_v1.py"
)
MIGRATION_MODULE = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(MIGRATION_MODULE)


def test_postgres_rehearsal_requires_real_postgresql_and_preserves_queue_rows():
    assert 'connection.dialect.name != "postgresql"' in SCRIPT
    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in SCRIPT
    assert "sys.path.insert(0, str(REPO_ROOT))" in SCRIPT
    assert 'alembic(BASELINE)' in SCRIPT and 'alembic(TARGET)' in SCRIPT
    assert 'before_digest != after_digest' in SCRIPT
    assert 'len(after_rows) != REHEARSAL_ROWS' in SCRIPT
    assert 'version != TARGET' in SCRIPT
    assert 'POSTGRES_MIGRATION_REHEARSAL=PASS' in SCRIPT
    assert "REHEARSAL_ROWS = 1000" in SCRIPT
    assert "TrafficMembership" in SCRIPT and "TrafficVisit" in SCRIPT
    assert "session.add_all([admin, channel])" in SCRIPT
    assert SCRIPT.index("session.add_all([admin, channel])") < SCRIPT.index("session.add(landing)")
    assert "INSERT INTO offline_conversion_queue (" in SCRIPT
    assert "metrica_upload_check_attempts" not in SCRIPT.split("INSERT INTO offline_conversion_queue (")[1].split("\"\"\"), {")[0]
    assert "session.add(OfflineConversion(" not in SCRIPT
    assert "session_replication_role" not in SCRIPT
    assert "BASELINE_ORM_COMPATIBILITY=PASS" in SCRIPT
    assert "BASELINE_AND_CANDIDATE_ORM=PASS" in SCRIPT
    assert "before_digest != after_digest" in SCRIPT


def test_0099_uses_bounded_lock_wait_concurrent_index_and_disables_destructive_downgrade():
    assert "SET LOCAL lock_timeout = '5s'" in MIGRATION
    assert "postgresql_concurrently=True" in MIGRATION
    assert "index.indisvalid and index.indisready" in MIGRATION
    assert "_is_exact_polling_index(index)" in MIGRATION
    assert "0099 downgrade is disabled" in MIGRATION
    assert "op.drop_column" not in MIGRATION


def test_0099_rejects_same_named_polling_indexes_with_wrong_catalog_semantics():
    expected = dict(
        column_names=["status", "metrica_upload_next_check_at"],
        indisunique=False,
        amname="btree",
        index_schema="public",
        table_schema="public",
        table_name="offline_conversion_queue",
        indnatts=2,
        indnkeyatts=2,
        no_predicate=True,
        no_expressions=True,
        index_definition=(
            "CREATE INDEX ix_offline_conversion_queue_metrica_poll "
            "ON public.offline_conversion_queue USING btree (status, metrica_upload_next_check_at)"
        ),
    )
    assert MIGRATION_MODULE._is_exact_polling_index(SimpleNamespace(**expected))

    for field, value in (
        ("indisunique", True),
        ("amname", "hash"),
        ("index_schema", "other"),
        ("table_schema", "other"),
        ("table_name", "other_table"),
        ("indnatts", 3),
        ("indnkeyatts", 1),
        ("no_predicate", False),
        ("no_expressions", False),
        ("index_definition", expected["index_definition"] + " WHERE status = 'pending'"),
    ):
        incompatible = {**expected, field: value}
        assert not MIGRATION_MODULE._is_exact_polling_index(SimpleNamespace(**incompatible)), field


def test_postgres_rehearsal_checks_exact_polling_index_definition_and_catalog_flags():
    assert "pg_get_indexdef(idx.oid)" in SCRIPT
    assert "i.indisunique" in SCRIPT
    assert "am.amname" in SCRIPT
    assert "i.indnatts" in SCRIPT and "i.indnkeyatts" in SCRIPT
    assert "i.indpred IS NULL" in SCRIPT and "i.indexprs IS NULL" in SCRIPT
    assert "index_signature != EXPECTED_INDEX_SIGNATURE" in SCRIPT
    assert '"true|true|false|btree|public|public|offline_conversion_queue|2|2|true|true|"' in SCRIPT
    assert 'f"0099 new-field defaults or exact polling index definition are invalid: "' in SCRIPT
    assert 'f"counter={new_counter_values}, index_signature={index_signature!r}"' in SCRIPT
