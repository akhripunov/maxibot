#!/usr/bin/env python3
"""Exercise the 0098→0099 migration against populated isolated PostgreSQL."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

BASELINE = "0098_durable_autopilot_execution_claim_v1"
TARGET = "0099_metrica_upload_reconciliation_v1"
PRODUCTION_BASE_SHA = "eda64bd2d0118009853e4e06f0a9c888ad19fd5a"
INDEX = "ix_offline_conversion_queue_metrica_poll"
EXPECTED_INDEX_SIGNATURE = (
    "t|t|f|btree|public|public|offline_conversion_queue|2|2|t|t|"
    "status,metrica_upload_next_check_at|CREATE INDEX ix_offline_conversion_queue_metrica_poll "
    "ON public.offline_conversion_queue USING btree (status, metrica_upload_next_check_at)"
)
NEW_COLUMNS = {
    "metrica_upload_counter_id",
    "metrica_upload_observation_started_at",
    "metrica_upload_check_attempts",
    "metrica_upload_next_check_at",
    "metrica_upload_check_error",
    "metrica_upload_recovered_at",
}
REHEARSAL_ROWS = 1000


def row_digest(rows: list[tuple]) -> str:
    encoded = json.dumps(
        [tuple(value.isoformat() if hasattr(value, "isoformat") else value for value in row) for row in rows],
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def check_baseline_model(engine, expected_rows: int) -> None:
    """Load the exact production-baseline ORM model and query the upgraded DB."""
    with tempfile.TemporaryDirectory(prefix="maxibot-0099-baseline-model-") as temp:
        source_root = Path(temp)
        for relative in ("app/__init__.py", "app/db/__init__.py", "app/db/base.py", "app/db/models.py"):
            result = subprocess.run(
                ["git", "show", f"{PRODUCTION_BASE_SHA}:{relative}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(result.stdout)
        script = r'''
import sys
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
sys.path.insert(0, sys.argv[1])
from app.db.models import OfflineConversion
engine = create_engine(sys.argv[2], pool_pre_ping=True)
with Session(engine) as session:
    rows = session.scalars(select(OfflineConversion).order_by(OfflineConversion.id)).all()
    assert len(rows) == int(sys.argv[3]), f"baseline ORM saw {len(rows)} rows"
    assert all(row.id and row.visit_id and row.membership_id for row in rows)
print(f"BASELINE_ORM_COMPATIBILITY=PASS ROWS={len(rows)}")
engine.dispose()
'''
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)
        subprocess.run(
            [sys.executable, "-c", script, str(source_root), os.environ["DATABASE_URL"], str(expected_rows)],
            check=True,
            env=environment,
            timeout=90,
        )


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith("postgresql+psycopg2://"):
        raise SystemExit("DATABASE_URL must point to the isolated PostgreSQL rehearsal service")

    def alembic(revision: str) -> None:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision], check=True, timeout=600)

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "postgresql":
                raise RuntimeError("migration rehearsal requires PostgreSQL")
            if inspect(connection).has_table("alembic_version"):
                raise RuntimeError("rehearsal database is not empty")

        alembic(BASELINE)
        from app.db.models import Admin, Channel, LandingPage, OfflineConversion, TrafficMembership, TrafficVisit

        before_columns = {column["name"] for column in inspect(engine).get_columns("offline_conversion_queue")}
        if before_columns & NEW_COLUMNS:
            raise RuntimeError("baseline schema already contains 0099 columns")
        if INDEX in {item["name"] for item in inspect(engine).get_indexes("offline_conversion_queue")}:
            raise RuntimeError("baseline schema already contains the 0099 index")

        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            admin = Admin(external_id="0099-rehearsal-admin")
            channel = Channel(admin=admin, external_id="0099-rehearsal-channel", title="Rehearsal", public_slug="rehearsal-0099")
            session.add_all([admin, channel])
            session.flush()
            landing = LandingPage(channel_id=channel.id, public_id="rehearsal-0099", title="Rehearsal", description="Isolated migration fixture")
            session.add(landing)
            session.flush()
            for index in range(REHEARSAL_ROWS):
                visit = TrafficVisit(
                    visit_token=f"0099-rehearsal-visit-{index}", landing_page_id=landing.id,
                    channel_id=channel.id, yclid=f"fixture-yclid-{index}",
                    metrica_client_id=f"fixture-client-{index}", landing_at=now,
                    visited_at=now, status="CONVERTED", destination_type="CHANNEL",
                )
                membership = TrafficMembership(
                    channel_id=channel.id, max_user_id=f"fixture-max-{index}", current_member=True,
                    joined_at=now, attribution_status="EXACT", attribution_reason="REHEARSAL_FIXTURE",
                    created_at=now, updated_at=now,
                )
                session.add_all([visit, membership])
                session.flush()
                session.add(OfflineConversion(
                    visit_id=visit.id, membership_id=membership.id, target="YANDEX",
                    goal="userDidSubscribe" if index % 2 == 0 else "userDidUnsubscribe",
                    conversion_datetime=now, status=("linkage_failure" if index % 3 == 0 else "pending"),
                    attempts=index % 4, metrica_upload_id=f"fixture-upload-{index}",
                    metrica_upload_status=("LINKAGE_FAILURE" if index % 3 == 0 else "PROCESSING"),
                    metrica_upload_checked_at=now,
                ))
                if index and index % 100 == 0:
                    session.flush()
            session.commit()

        old_columns = (
            "id", "visit_id", "membership_id", "target", "goal", "identifier_type", "identifier_value",
            "conversion_datetime", "status", "attempts", "next_retry_at", "last_error", "created_at",
            "sent_at", "metrica_upload_id", "metrica_upload_status", "metrica_upload_checked_at",
        )
        with engine.connect() as connection:
            before_rows = connection.execute(text(
                "SELECT " + ",".join(old_columns) + " FROM offline_conversion_queue ORDER BY id"
            )).all()
            if len(before_rows) != REHEARSAL_ROWS:
                raise RuntimeError(f"expected {REHEARSAL_ROWS} FK-valid conversion rows before migration; got {len(before_rows)}")
            if connection.execute(text("SELECT count(*) FROM traffic_visits")).scalar_one() != REHEARSAL_ROWS:
                raise RuntimeError("rehearsal visit fixture cardinality mismatch")
            if connection.execute(text("SELECT count(*) FROM traffic_memberships")).scalar_one() != REHEARSAL_ROWS:
                raise RuntimeError("rehearsal membership fixture cardinality mismatch")
            before_digest = row_digest(before_rows)
            before_head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if before_head != BASELINE:
                raise RuntimeError(f"expected Alembic head {BASELINE}, got {before_head}")

        alembic(TARGET)
        inspector = inspect(engine)
        after_columns = {column["name"] for column in inspector.get_columns("offline_conversion_queue")}
        indexes = {item["name"] for item in inspector.get_indexes("offline_conversion_queue")}
        if not NEW_COLUMNS.issubset(after_columns) or INDEX not in indexes:
            raise RuntimeError("0099 columns or polling index are missing")
        attempts = next(column for column in inspector.get_columns("offline_conversion_queue")
                        if column["name"] == "metrica_upload_check_attempts")
        if attempts["nullable"] or str(attempts.get("default")) not in {"0", "'0'::integer"}:
            raise RuntimeError("new check-attempt counter is not non-null with default zero")
        with engine.connect() as connection:
            after_rows = connection.execute(text(
                "SELECT " + ",".join(old_columns) + " FROM offline_conversion_queue ORDER BY id"
            )).all()
            after_digest = row_digest(after_rows)
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            new_counter_values = connection.execute(text(
                "SELECT count(*) FROM offline_conversion_queue WHERE metrica_upload_check_attempts <> 0"
            )).scalar_one()
            index_signature = connection.execute(text("""
                SELECT concat_ws('|', i.indisvalid::text, i.indisready::text,
                    i.indisunique::text, am.amname, index_ns.nspname,
                    table_ns.nspname, tbl.relname, i.indnatts::text,
                    i.indnkeyatts::text, (i.indpred IS NULL)::text,
                    (i.indexprs IS NULL)::text,
                    array_to_string(array_agg(coalesce(a.attname, '<expression>')
                        ORDER BY k.ordinality), ','), pg_get_indexdef(idx.oid))
                FROM pg_class idx
                JOIN pg_index i ON i.indexrelid=idx.oid
                JOIN pg_am am ON am.oid=idx.relam
                JOIN pg_namespace index_ns ON index_ns.oid=idx.relnamespace
                JOIN pg_class tbl ON tbl.oid=i.indrelid
                JOIN pg_namespace table_ns ON table_ns.oid=tbl.relnamespace
                JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON true
                LEFT JOIN pg_attribute a ON a.attrelid=tbl.oid AND a.attnum=k.attnum
                WHERE idx.relname=:index_name AND index_ns.nspname='public'
                GROUP BY i.indisvalid, i.indisready, i.indisunique, am.amname,
                    index_ns.nspname, table_ns.nspname, tbl.relname,
                    i.indnatts, i.indnkeyatts, (i.indpred IS NULL),
                    (i.indexprs IS NULL), idx.oid
            """), {"index_name": INDEX}).scalar_one_or_none()
        if before_digest != after_digest or len(after_rows) != REHEARSAL_ROWS or version != TARGET:
            raise RuntimeError("0099 altered existing conversion data or did not reach the exact target head")
        if new_counter_values != 0 or index_signature != EXPECTED_INDEX_SIGNATURE:
            raise RuntimeError("new-field defaults or exact polling index definition are invalid")

        check_baseline_model(engine, REHEARSAL_ROWS)
        from app.db.models import OfflineConversion as CandidateOfflineConversion
        with Session(engine) as session:
            candidate_rows = session.scalars(select(CandidateOfflineConversion).order_by(CandidateOfflineConversion.id)).all()
            if len(candidate_rows) != REHEARSAL_ROWS or any(row.metrica_upload_check_attempts != 0 for row in candidate_rows):
                raise RuntimeError("candidate ORM compatibility query failed against migrated rows")

        print(
            f"POSTGRES_MIGRATION_REHEARSAL=PASS PATH={BASELINE}->{TARGET} "
            f"FK_VALID_ROWS={len(after_rows)} DATA_SHA256={after_digest} INDEX={INDEX} "
            "BASELINE_AND_CANDIDATE_ORM=PASS"
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"POSTGRES_MIGRATION_REHEARSAL=FAIL {type(error).__name__}: {error}", file=sys.stderr)
        raise
