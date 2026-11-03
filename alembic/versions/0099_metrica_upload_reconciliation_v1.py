"""Persist the original Metrica counter and bounded recheck state."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0099_metrica_upload_reconciliation_v1"
down_revision = "0098_durable_autopilot_execution_claim_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bound ACCESS EXCLUSIVE lock acquisition on the existing queue table.
    # If a conflicting transaction is active, fail before changing the schema.
    op.execute("SET LOCAL lock_timeout = '5s'")
    expected = (
        sa.Column("metrica_upload_counter_id", sa.String(length=32), nullable=True),
        sa.Column("metrica_upload_observation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrica_upload_check_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metrica_upload_next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrica_upload_check_error", sa.String(length=64), nullable=True),
        sa.Column("metrica_upload_recovered_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind = op.get_bind()
    existing = {column["name"]: column for column in inspect(bind).get_columns("offline_conversion_queue")}
    for definition in expected:
        actual = existing.get(definition.name)
        if actual is None:
            op.add_column("offline_conversion_queue", definition)
        elif (actual["type"]._type_affinity is not definition.type._type_affinity
              or getattr(actual["type"], "length", None) != getattr(definition.type, "length", None)
              or actual["nullable"] != definition.nullable):
            raise RuntimeError(f"0099 partial recovery found incompatible column {definition.name}")
        elif isinstance(definition.type, sa.DateTime) and actual["type"].timezone != definition.type.timezone:
            raise RuntimeError(f"0099 partial recovery found incompatible timezone on {definition.name}")

    attempts = inspect(bind).get_columns("offline_conversion_queue")
    attempts = next(column for column in attempts if column["name"] == "metrica_upload_check_attempts")
    if str(attempts.get("default")) not in {"0", "'0'::integer"}:
        raise RuntimeError("0099 partial recovery found an incompatible check-attempt default")

    index = bind.execute(sa.text("""
        SELECT i.indisvalid, i.indisready,
               array_agg(a.attname ORDER BY k.ordinality) AS column_names
        FROM pg_class idx
        JOIN pg_index i ON i.indexrelid = idx.oid
        JOIN pg_class tbl ON tbl.oid = i.indrelid
        JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON true
        JOIN pg_attribute a ON a.attrelid = tbl.oid AND a.attnum = k.attnum
        WHERE idx.relname = 'ix_offline_conversion_queue_metrica_poll'
          AND tbl.relname = 'offline_conversion_queue'
        GROUP BY i.indisvalid, i.indisready
    """)).first()
    if index is not None:
        names = list(index.column_names)
        if names != ["status", "metrica_upload_next_check_at"]:
            raise RuntimeError("0099 partial recovery found an index with an incompatible column definition")
        if index.indisvalid and index.indisready:
            return
        if index.indisvalid:
            raise RuntimeError("0099 partial recovery found an unexpected valid/not-ready polling index")
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_offline_conversion_queue_metrica_poll")

    # Avoid holding a write-blocking table lock while indexing a live queue.
    # This is the final operation. If it is interrupted, a later authorized
    # retry validates/reuses completed columns and repairs an invalid index.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_offline_conversion_queue_metrica_poll",
            "offline_conversion_queue",
            ["status", "metrica_upload_next_check_at"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    raise RuntimeError("0099 downgrade is disabled; preserve reconciliation data and repair forward")
