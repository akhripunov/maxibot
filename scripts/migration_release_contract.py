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
        "preserved_tables", "frozen_paths", "resume_allowed", "recovery", "schema",
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
    changed_migration_paths = sorted(path for path in changed if path.startswith("alembic/versions/"))
    if changed_migration_paths != sorted(chain):
        raise ValueError("changed Alembic revision files must exactly match the authorized source-to-target chain")
    if CONTRACT_PATH not in changed:
        raise ValueError("migration candidate must include its declared risk contract")
    rehearsal_script = contract["rehearsal_script"]
    if not isinstance(rehearsal_script, str) or rehearsal_script not in changed or not rehearsal_script.startswith("scripts/"):
        raise ValueError("migration rehearsal script must be an approved candidate path")
    tables = contract["preserved_tables"]
    if not isinstance(tables, list) or not tables or any(not isinstance(name, str) or not SQL_NAME_RE.fullmatch(name) for name in tables):
        raise ValueError("migration must declare safe preserved-table identifiers")
    if len(tables) != len(set(tables)):
        raise ValueError("migration preserved-table list contains duplicates")
    _validate_schema_contract(contract["schema"])
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
        "schema": contract["schema"],
        "task": contract["task"],
    }


def _validate_schema_contract(schema: dict) -> None:
    if not isinstance(schema, dict) or set(schema) != {"source", "target"}:
        raise ValueError("migration schema contract must define exactly source and target states")
    source = schema["source"]
    target = schema["target"]
    source_required = {"required_columns", "absent_columns", "absent_indexes", "absent_constraints"}
    source_optional = {"recoverable_indexes"}
    if (not isinstance(source, dict) or not source_required <= set(source)
            or set(source) - source_required - source_optional
            or not isinstance(target, dict) or set(target) != {"columns", "indexes", "constraints"}):
        raise ValueError("migration schema contract source/target fields are malformed")
    for key in ("required_columns", "absent_columns"):
        items = source[key]
        if not isinstance(items, list):
            raise ValueError(f"migration schema source {key} must be a list")
        for item in items:
            if not isinstance(item, dict) or not {"table", "name"} <= set(item):
                raise ValueError(f"migration schema source {key} has a malformed column")
            if not SQL_NAME_RE.fullmatch(item["table"]) or not SQL_NAME_RE.fullmatch(item["name"]):
                raise ValueError(f"migration schema source {key} has an unsafe identifier")
            if key == "required_columns" and (not isinstance(item.get("data_type"), str) or not item["data_type"]):
                raise ValueError("migration schema required source columns need a data_type")
    for key in ("absent_indexes", "absent_constraints"):
        names = source[key]
        if not isinstance(names, list) or any(not isinstance(name, str) or not SQL_NAME_RE.fullmatch(name) for name in names):
            raise ValueError(f"migration schema source {key} must contain safe names")
    for item in target["columns"]:
        required = {"table", "name", "data_type", "nullable"}
        if not isinstance(item, dict) or not required <= set(item) or set(item) - required - {"max_length", "default_any"}:
            raise ValueError("migration schema target contains a malformed column postcondition")
        if not SQL_NAME_RE.fullmatch(item["table"]) or not SQL_NAME_RE.fullmatch(item["name"]):
            raise ValueError("migration schema target has an unsafe column identifier")
        if not isinstance(item["data_type"], str) or not item["data_type"] or not isinstance(item["nullable"], bool):
            raise ValueError("migration schema target column postcondition has invalid types")
        if "max_length" in item and item["max_length"] is not None and (not isinstance(item["max_length"], int) or item["max_length"] < 1):
            raise ValueError("migration schema target max_length is invalid")
        if "default_any" in item and (not isinstance(item["default_any"], list) or not item["default_any"]):
            raise ValueError("migration schema target default_any must be a non-empty list")
    for item in target["indexes"]:
        required = {"table", "name", "definition", "method", "unique", "valid", "ready"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("migration schema target contains a malformed index postcondition")
        if any(not isinstance(item[key], str) or not item[key] for key in ("table", "name", "definition", "method")):
            raise ValueError("migration schema target index postcondition has invalid names or definition")
        if not SQL_NAME_RE.fullmatch(item["table"]) or not SQL_NAME_RE.fullmatch(item["name"]):
            raise ValueError("migration schema target has an unsafe index identifier")
        if any(not isinstance(item[key], bool) for key in ("unique", "valid", "ready")):
            raise ValueError("migration schema target index flags must be boolean")
        if item["valid"] is not True or item["ready"] is not True:
            raise ValueError("migration schema target indexes must be valid and ready")
    target_index_keys = [(item["table"], item["name"]) for item in target["indexes"]]
    if len(target_index_keys) != len(set(target_index_keys)):
        raise ValueError("migration schema target index postconditions contain duplicate identities")
    recoverable_indexes = source.get("recoverable_indexes", [])
    if not isinstance(recoverable_indexes, list):
        raise ValueError("migration schema source recoverable_indexes must be a list")
    recoverable_keys = []
    for item in recoverable_indexes:
        if not isinstance(item, dict) or set(item) != {"table", "name"}:
            raise ValueError("migration schema source has a malformed recoverable index")
        if not all(isinstance(item[key], str) and SQL_NAME_RE.fullmatch(item[key]) for key in ("table", "name")):
            raise ValueError("migration schema source recoverable index has an unsafe identity")
        key = (item["table"], item["name"])
        if key not in target_index_keys:
            raise ValueError("recoverable source index must match an exact target index postcondition")
        recoverable_keys.append(key)
    if len(recoverable_keys) != len(set(recoverable_keys)):
        raise ValueError("migration schema source recoverable indexes contain duplicate identities")
    absent_index_names = set(source["absent_indexes"])
    recoverable_index_names = {name for _, name in recoverable_keys}
    if absent_index_names & recoverable_index_names:
        raise ValueError("source index cannot be both absent and recoverable")
    for item in target["constraints"]:
        required = {"table", "name", "type", "definition", "validated"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("migration schema target contains a malformed constraint postcondition")
        if (not SQL_NAME_RE.fullmatch(item["table"]) or not SQL_NAME_RE.fullmatch(item["name"])
                or not isinstance(item["type"], str) or not item["type"]
                or not isinstance(item["definition"], str) or not item["definition"]
                or not isinstance(item["validated"], bool)):
            raise ValueError("migration schema target constraint postcondition is invalid")
    if not any(source[key] for key in ("required_columns", "absent_columns", "absent_indexes", "absent_constraints")) and not source.get("recoverable_indexes"):

        raise ValueError("migration schema contract must declare at least one source-state precondition")
    if not any(target[key] for key in ("columns", "indexes", "constraints")):
        raise ValueError("migration schema contract must declare at least one target-state postcondition")


def validate_schema_snapshot(schema: dict, snapshot: dict, mode: str) -> None:
    """Fail closed unless the DB catalog is either a clean source or exact contract target."""
    _validate_schema_contract(schema)
    if mode not in {"source", "target"}:
        raise ValueError("migration schema snapshot mode must be source or target")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("columns"), list) or not isinstance(snapshot.get("indexes"), list):
        raise ValueError("migration schema catalog snapshot is malformed")
    columns = {}
    for item in snapshot["columns"]:
        if not isinstance(item, dict) or not isinstance(item.get("table"), str) or not isinstance(item.get("name"), str):
            raise ValueError("migration schema catalog contains a malformed column")
        key = (item["table"], item["name"])
        if key in columns:
            raise ValueError("migration schema catalog contains a duplicate column")
        columns[key] = item
    indexes = {}
    for item in snapshot["indexes"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("table"), str):
            raise ValueError("migration schema catalog contains a malformed index")
        key = (item["table"], item["name"])
        if key in indexes:
            raise ValueError("migration schema catalog contains a duplicate index")
        indexes[key] = item
    constraints = {}
    for item in snapshot.get("constraints", []):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("table"), str):
            raise ValueError("migration schema catalog contains a malformed constraint")
        key = (item["table"], item["name"])
        if key in constraints:
            raise ValueError("migration schema catalog contains a duplicate constraint")
        constraints[key] = item

    if mode == "source":
        for expected in schema["source"]["required_columns"]:
            actual = columns.get((expected["table"], expected["name"]))
            if actual is None or actual.get("data_type") != expected["data_type"]:
                raise ValueError(f"migration source schema is missing or mismatches {expected['table']}.{expected['name']}")
        for expected in schema["source"]["absent_columns"]:
            if (expected["table"], expected["name"]) in columns:
                raise ValueError(f"migration source schema already contains target column {expected['table']}.{expected['name']}")
        for name in schema["source"]["absent_indexes"]:
            if any(actual_name == name for _, actual_name in indexes):
                raise ValueError(f"migration source schema already contains target index {name}")
        target_indexes = {
            (item["table"], item["name"]): item for item in schema["target"]["indexes"]
        }
        for expected in schema["source"].get("recoverable_indexes", []):
            matches = [item for (_, name), item in indexes.items() if name == expected["name"]]
            if len(matches) > 1:
                raise ValueError(f"migration source schema has ambiguous recoverable index {expected['name']}")
            if not matches:
                continue
            actual = matches[0]
            target = target_indexes[(expected["table"], expected["name"])]
            if actual.get("table") != expected["table"]:
                raise ValueError(f"migration source schema recoverable index {expected['name']} is attached to the wrong table")
            if any(actual.get(key) != target[key] for key in ("definition", "method", "unique")):
                raise ValueError(f"migration source schema recoverable index {expected['name']} has incompatible catalog semantics")
            if not isinstance(actual.get("valid"), bool) or not isinstance(actual.get("ready"), bool):
                raise ValueError(f"migration source schema recoverable index {expected['name']} has malformed catalog flags")
            if actual["valid"] and not actual["ready"]:
                raise ValueError(f"migration source schema recoverable index {expected['name']} is valid but not ready")
        for name in schema["source"]["absent_constraints"]:
            if any(actual_name == name for _, actual_name in constraints):
                raise ValueError(f"migration source schema already contains target constraint {name}")
        return

    for expected in schema["target"]["columns"]:
        actual = columns.get((expected["table"], expected["name"]))
        if actual is None:
            raise ValueError(f"migration target schema is missing {expected['table']}.{expected['name']}")
        for key in ("data_type", "max_length", "nullable"):
            if key in expected and actual.get(key) != expected[key]:
                raise ValueError(f"migration target column {expected['table']}.{expected['name']} has unexpected {key}")
        if "default_any" in expected and actual.get("default") not in expected["default_any"]:
            raise ValueError(f"migration target column {expected['table']}.{expected['name']} has unexpected default")
    for expected in schema["target"]["indexes"]:
        actual = indexes.get((expected["table"], expected["name"]))
        if actual is None or any(actual.get(key) != expected[key] for key in ("definition", "method", "unique", "valid", "ready")):
            raise ValueError(f"migration target index {expected['name']} does not match its exact valid postcondition")
    for expected in schema["target"]["constraints"]:
        actual = constraints.get((expected["table"], expected["name"]))
        if actual is None or any(actual.get(key) != expected[key] for key in ("type", "definition", "validated")):
            raise ValueError(f"migration target constraint {expected['name']} does not match its exact postcondition")


def validate_owner_main_publication(ref: str, actor: str, repository_owner: str) -> None:
    """Bind production migration authorization to the owner publishing protected main."""
    if ref != "refs/heads/main" or not actor or actor.casefold() != repository_owner.casefold():
        raise ValueError("production migration requires the repository owner to publish the exact contract on protected main")


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
