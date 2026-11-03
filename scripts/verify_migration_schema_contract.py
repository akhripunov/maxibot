#!/usr/bin/env python3
"""Print and validate contract-driven PostgreSQL catalog snapshots."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.migration_release_contract import validate_schema_snapshot


SCHEMA_SNAPSHOT_SQL = """
SELECT json_build_object(
  'columns', (
    SELECT COALESCE(json_agg(json_build_object(
      'table', table_name, 'name', column_name, 'data_type', data_type,
      'max_length', character_maximum_length,
      'nullable', (is_nullable = 'YES'), 'default', column_default
    ) ORDER BY table_name, ordinal_position), '[]'::json)
    FROM information_schema.columns WHERE table_schema = 'public'
  ),
  'indexes', (
    SELECT COALESCE(json_agg(json_build_object(
      'table', tbl.relname, 'name', idx.relname,
      'definition', pg_get_indexdef(idx.oid), 'method', am.amname,
      'unique', i.indisunique, 'valid', i.indisvalid, 'ready', i.indisready
    ) ORDER BY tbl.relname, idx.relname), '[]'::json)
    FROM pg_class idx
    JOIN pg_index i ON i.indexrelid = idx.oid
    JOIN pg_am am ON am.oid = idx.relam
    JOIN pg_class tbl ON tbl.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = idx.relnamespace
    WHERE n.nspname = 'public'
  ),
  'constraints', (
    SELECT COALESCE(json_agg(json_build_object(
      'table', tbl.relname, 'name', con.conname,
      'type', con.contype::text, 'definition', pg_get_constraintdef(con.oid),
      'validated', con.convalidated
    ) ORDER BY tbl.relname, con.conname), '[]'::json)
    FROM pg_constraint con
    JOIN pg_class tbl ON tbl.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = tbl.relnamespace
    WHERE n.nspname = 'public'
  )
)::text;
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-query", action="store_true")
    parser.add_argument("--contract-b64")
    parser.add_argument("--snapshot-b64")
    parser.add_argument("--snapshot-stdin", action="store_true")
    parser.add_argument("--mode", choices=("source", "target"))
    args = parser.parse_args()
    if args.print_query:
        print(SCHEMA_SNAPSHOT_SQL)
        return 0
    if not args.contract_b64 or not args.mode:
        parser.error("contract-b64 and mode are required for validation")
    if (args.snapshot_b64 is None) == (not args.snapshot_stdin):
        parser.error("provide exactly one of snapshot-b64 or snapshot-stdin")
    contract = json.loads(base64.b64decode(args.contract_b64, validate=True))
    snapshot = json.loads(sys.stdin.read()) if args.snapshot_stdin else json.loads(base64.b64decode(args.snapshot_b64, validate=True))
    validate_schema_snapshot(contract, snapshot, args.mode)
    print(f"MIGRATION_SCHEMA_{args.mode.upper()}=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, json.JSONDecodeError) as error:
        print(f"MIGRATION_SCHEMA_CONTRACT=BLOCKED:{error}", file=sys.stderr)
        raise SystemExit(1)
