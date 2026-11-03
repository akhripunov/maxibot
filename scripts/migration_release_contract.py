#!/usr/bin/env python3
"""Validate a task-scoped database migration contract against the candidate diff."""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = "docs/release/migration-risk-contract.json"
REVISION_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SQL_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
SCHEMA_PATHS = ("alembic/versions/", "app/db/models.py")
FORBIDDEN_MIGRATION_PATHS = {"docker-compose.vps.yml"}


def load_alembic_graph(directory: Path) -> list[dict]:
    graph = []
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as error:
            raise ValueError(f"cannot parse Alembic revision {path.name}: {error}") from error
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name in {"revision", "down_revision"}:
                    try:
                        values[name] = ast.literal_eval(node.value)
                    except (ValueError, TypeError) as error:
                        raise ValueError(f"revision metadata must be literal in {path.name}") from error
        revision = values.get("revision")
        if revision is None:
            continue
        parent = values.get("down_revision")
        parents = list(parent) if isinstance(parent, (tuple, list)) else ([] if parent is None else [parent])
        graph.append({"revision": revision, "parents": parents, "path": path.relative_to(ROOT).as_posix()})
    return graph


def resolve_linear_chain(graph: list[dict], source_revision: str, target_revision: str) -> list[str]:
    by_revision = {}
    for node in graph:
        revision = node.get("revision")
        if not isinstance(revision, str) or revision in by_revision:
            raise ValueError("Alembic graph contains a missing or duplicate revision")
        by_revision[revision] = node
    if source_revision not in by_revision or target_revision not in by_revision:
        raise ValueError("source or target revision is absent from the candidate Alembic graph")
    if source_revision == target_revision:
        raise ValueError("migration source and target revisions must differ")

    reversed_paths = []
    current = target_revision
    seen = set()
    while current != source_revision:
        if current in seen:
            raise ValueError("Alembic graph contains a cycle")
        seen.add(current)
        node = by_revision[current]
        parents = node.get("parents", [])
        if len(parents) != 1:
            raise ValueError(f"migration path is not linear at revision {current}")
        reversed_paths.append(node["path"])
        current = parents[0]
        if current not in by_revision:
            raise ValueError(f"migration source {source_revision} is not an ancestor of target {target_revision}")

    children = [node["revision"] for node in graph if source_revision in node.get("parents", [])]
    if len(children) > 1:
        raise ValueError(f"Alembic source {source_revision} has competing child revisions")
    parent_revisions = {parent for node in graph for parent in node.get("parents", [])}
    heads = {node["revision"] for node in graph} - parent_revisions
    if heads != {target_revision}:
        raise ValueError(f"candidate Alembic graph must have exactly the authorized target as its head; found {sorted(heads)}")
    return list(reversed(reversed_paths))


def evaluate_contract(
    contract: dict,
    changed_paths: list[str],
    production_sha: str,
    database_head: str,
    graph: list[dict],
) -> dict:
    changed = sorted(set(changed_paths))
    if any(path in FORBIDDEN_MIGRATION_PATHS for path in changed):
        raise ValueError("production Compose changes are forbidden in a migration release")
    schema_change = any(path.startswith(SCHEMA_PATHS[0]) or path == SCHEMA_PATHS[1] for path in changed)
    migration_related_change = any(path.startswith("alembic/") or path == "alembic.ini" for path in changed)
    if not schema_change:
        if CONTRACT_PATH in changed or migration_related_change or "docker-compose.vps.yml" in changed:
            raise ValueError("migration contract, Alembic configuration, or production Compose changed without a declared schema migration")
        return {"migration_required": False, "migration_resume": False}
    required = {
        "version", "task", "owner_authorization", "source_application_sha", "source_revision",
        "target_revision", "migration_paths", "approved_paths", "rehearsal_script",
        "preserved_tables", "frozen_paths", "resume_allowed", "recovery",
    }
    if not required <= set(contract):
        raise ValueError(f"migration contract is missing fields: {sorted(required - set(contract))}")
    if contract["version"] != 1:
        raise ValueError("unsupported migration contract version")
    if not isinstance(contract["task"], str) or not contract["task"].strip():
        raise ValueError("migration contract task reference is required")
    authorization = contract["owner_authorization"]
    if not isinstance(authorization, dict) or authorization.get("status") != "OWNER_AUTHORIZED":
        raise ValueError("migration is not marked OWNER_AUTHORIZED")
    if not isinstance(authorization.get("reference"), str) or not authorization["reference"].strip():
        raise ValueError("migration OWNER authorization reference is required")
    if not isinstance(authorization.get("risk_category"), str) or not authorization["risk_category"].strip():
        raise ValueError("migration OWNER risk category is required")
    if not SHA_RE.fullmatch(contract["source_application_sha"]):
        raise ValueError("migration source application SHA is malformed")
    if production_sha != contract["source_application_sha"]:
        raise ValueError("production application SHA differs from the authorized migration baseline")

    source_revision = contract["source_revision"]
    target_revision = contract["target_revision"]
    if not REVISION_RE.fullmatch(source_revision) or not REVISION_RE.fullmatch(target_revision):
        raise ValueError("migration source or target revision is malformed")
    chain = resolve_linear_chain(graph, source_revision, target_revision)
    declared_migrations = contract["migration_paths"]
    if not isinstance(declared_migrations, list) or declared_migrations != chain:
        raise ValueError("contract migration paths do not exactly match the linear Alembic source-to-target graph")

    approved = contract["approved_paths"]
    if not isinstance(approved, list) or any(not isinstance(path, str) or not path for path in approved):
        raise ValueError("migration contract approved paths must be non-empty strings")
    if len(approved) != len(set(approved)) or sorted(approved) != changed:
        raise ValueError("candidate diff does not exactly match the migration contract approved paths")
    frozen_paths = contract["frozen_paths"]
    if not isinstance(frozen_paths, list) or len(frozen_paths) != len(set(frozen_paths)) or not set(frozen_paths) <= set(approved):
        raise ValueError("migration contract frozen paths must be unique paths inside the approved candidate scope")
    if not set(chain) <= set(changed) or CONTRACT_PATH not in changed:
        raise ValueError("migration candidate must include its declared Alembic path and risk contract")
    rehearsal_script = contract["rehearsal_script"]
    if not isinstance(rehearsal_script, str) or rehearsal_script not in changed or not rehearsal_script.startswith("scripts/"):
        raise ValueError("migration rehearsal script must be an approved candidate path")
    tables = contract["preserved_tables"]
    if not isinstance(tables, list) or not tables or any(not isinstance(name, str) or not SQL_NAME_RE.fullmatch(name) for name in tables):
        raise ValueError("migration must declare safe preserved-table identifiers")
    if len(tables) != len(set(tables)):
        raise ValueError("migration preserved-table list contains duplicates")
    if not isinstance(contract["resume_allowed"], bool):
        raise ValueError("migration resume_allowed must be boolean")
    recovery = contract["recovery"]
    if not isinstance(recovery, dict) or recovery.get("database") != "forward_only":
        raise ValueError("migration database recovery must be explicitly forward-only")
    if recovery.get("application_rollback_sha") != contract["source_application_sha"]:
        raise ValueError("migration recovery must name the exact source application SHA")

    if database_head == source_revision:
        resume = False
    elif database_head == target_revision and contract["resume_allowed"] is True:
        resume = True
    else:
        raise ValueError("database head is neither the exact authorized source nor an allowed completed target")
    return {
        "migration_required": True,
        "migration_resume": resume,
        "source_revision": source_revision,
        "target_revision": target_revision,
        "migration_paths": chain,
        "approved_paths": approved,
        "frozen_paths": frozen_paths,
        "rehearsal_script": rehearsal_script,
        "preserved_tables": tables,
        "task": contract["task"],
    }


def _git_paths(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", base, head], cwd=ROOT,
        check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout.splitlines()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--database-head", required=True)
    args = parser.parse_args()
    if not SHA_RE.fullmatch(args.base_sha) or not SHA_RE.fullmatch(args.head_sha):
        raise ValueError("candidate base/head SHA is malformed")
    contract = json.loads((ROOT / CONTRACT_PATH).read_text(encoding="utf-8"))
    result = evaluate_contract(
        contract,
        _git_paths(args.base_sha, args.head_sha),
        args.base_sha,
        args.database_head,
        load_alembic_graph(ROOT / "alembic/versions"),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"MIGRATION_RELEASE_CONTRACT=BLOCKED:{error}", file=sys.stderr)
        raise SystemExit(1)
