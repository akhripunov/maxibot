#!/usr/bin/env python3
"""Run a non-production rehearsal for a candidate Alembic migration."""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ADS_TABLES = (
    "ads_accounts", "ads_campaigns", "ads_promotion_campaign_links", "ads_collection_runs",
    "ads_analysis_snapshots", "ads_ai_runs", "ads_recommendations", "ads_memories",
    "ads_data_health", "ads_ai_outcomes", "ads_memory_evidence", "ads_creatives",
    "ads_entities", "ads_report_runs", "ads_observations", "ads_daily_aggregates",
)


def _revision_graph(sha: str) -> dict[str, tuple[str, ...]]:
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", sha, "alembic/versions"], text=True
    ).splitlines()
    graph: dict[str, tuple[str, ...]] = {}
    for name in names:
        if not name.endswith(".py") or "/__pycache__/" in name:
            continue
        text = subprocess.check_output(["git", "show", f"{sha}:{name}"], text=True)
        rev = re.search(r"^revision\s*=\s*([^\n]+)", text, re.M)
        down = re.search(r"^down_revision\s*=\s*([^\n]+)", text, re.M)
        if not rev:
            continue
        revision = ast.literal_eval(rev.group(1).strip())
        parent = ast.literal_eval(down.group(1).strip()) if down else None
        parents = () if parent is None else ((parent,) if isinstance(parent, str) else tuple(parent))
        if isinstance(revision, str) and all(isinstance(item, str) for item in parents):
            graph[revision] = parents
    return graph


def _path(graph: dict[str, tuple[str, ...]], current: str, target: str) -> list[str]:
    if current not in graph:
        raise RuntimeError(f"current production migration is absent from candidate graph: {current}")
    if target not in graph:
        raise RuntimeError(f"target migration is absent from candidate graph: {target}")
    children = {revision: [] for revision in graph}
    for revision, parents in graph.items():
        for parent in parents:
            if parent not in graph:
                raise RuntimeError(f"migration graph references unknown parent: {parent}")
            children[parent].append(revision)
    paths: list[list[str]] = []
    def walk(node: str, chain: list[str], seen: set[str]) -> None:
        if node == target:
            paths.append(chain)
            return
        for child in sorted(children[node]):
            if child in seen:
                raise RuntimeError("migration graph contains a cycle")
            walk(child, chain + [child], seen | {child})
    walk(current, [current], {current})
    if not paths:
        raise RuntimeError(f"no forward migration path from {current} to {target}")
    if len(paths) != 1:
        raise RuntimeError(f"ambiguous migration path from {current} to {target}: {len(paths)} paths")
    return paths[0]


def _rehearse_migration(sha: str, target: str) -> None:
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", sha, "alembic/versions"], text=True
    ).splitlines()
    migration_file = None
    for name in names:
        if not name.endswith(".py"):
            continue
        text = subprocess.check_output(["git", "show", f"{sha}:{name}"], text=True)
        if re.search(rf'^revision\s*=\s*["\']{re.escape(target)}["\']', text, re.M):
            migration_file = (name, text)
            break
    if migration_file is None:
        raise RuntimeError("target migration file not found")
    migration_text = migration_file[1]
    with tempfile.TemporaryDirectory(prefix="maxibot-migration-") as directory:
        from sqlalchemy import create_engine, text
        engine = create_engine(f"sqlite:///{Path(directory) / 'rehearsal.sqlite'}")
        db = engine.connect()
        for table in ADS_TABLES:
            db.execute(text(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)'))
        for table in (
            "ads_promotion_campaign_links", "ads_collection_runs", "ads_analysis_snapshots",
            "ads_ai_runs", "ads_recommendations", "ads_memories", "ads_data_health",
        ):
            db.execute(text(f'INSERT INTO "{table}" (id) VALUES (1)'))
        if 'op.add_column("traffic_profiles"' in migration_text:
            db.execute(text("CREATE TABLE traffic_profiles (id INTEGER PRIMARY KEY, channel_id VARCHAR(255))"))
        db.commit()
        path = Path(directory) / "candidate_migration.py"
        path.write_text(migration_text)
        spec = importlib.util.spec_from_file_location("candidate_migration", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        context = MigrationContext.configure(db)
        with Operations.context(context):
            module.upgrade()
        if "destination_platform" in migration_text:
            for table in (
                "ads_promotion_campaign_links", "ads_collection_runs", "ads_analysis_snapshots",
                "ads_ai_runs", "ads_recommendations", "ads_memories", "ads_data_health",
            ):
                value = db.execute(text(f'SELECT destination_platform FROM "{table}" WHERE id=1')).fetchone()[0]
                if value != "MAX":
                    raise RuntimeError(f"backfill invalid for {table}: {value!r}")
        if 'op.add_column("traffic_profiles"' in migration_text:
            columns = {row[1] for row in db.execute(text("PRAGMA table_info(traffic_profiles)"))}
            required = {
                "yandex_metrica_oauth_token_encrypted", "yandex_metrica_oauth_connected_at",
                "yandex_metrica_oauth_client_fingerprint", "yandex_metrica_oauth_scopes",
                "yandex_direct_oauth_token_encrypted", "yandex_direct_oauth_connected_at",
                "yandex_direct_oauth_client_fingerprint", "yandex_direct_oauth_scopes",
            }
            if not required.issubset(columns):
                raise RuntimeError(f"OAuth split columns missing: {sorted(required - columns)}")
        count = db.execute(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name LIKE 'ads_%'")).fetchone()[0]
        if count != len(ADS_TABLES):
            raise RuntimeError(f"ADS table count changed: {count}")
        db.close()
        engine.dispose()


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: migration_rehearsal.py SHA CURRENT_HEAD TARGET", file=sys.stderr)
        return 2
    sha, current, target = sys.argv[1:]
    try:
        path = _path(_revision_graph(sha), current, target)
        _rehearse_migration(sha, target)
    except Exception as exc:
        print(f"MIGRATION_REHEARSAL=BLOCKED {exc}", file=sys.stderr)
        return 1
    print(f"MIGRATION_REHEARSAL=PASS PATH={'->'.join(path)} ADS_TABLE_COUNT={len(ADS_TABLES)} BACKFILL=MAX")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
