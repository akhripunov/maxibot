#!/usr/bin/env python3
"""Publish bounded MAXIBOT release facts to the project Handoff and journal."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone

from sync_agents_to_linear import SyncError, _content_matches, graphql_request

HANDOFF_ID = "6f799a5c-75b2-4fc1-a522-86bab4c1950b"
HANDOFF_TITLE = "Архитектор — Handoff"
JOURNAL_ID = "13df4e43-f252-424c-afca-fdb2a37eaf29"
JOURNAL_TITLE = "Журнал релизов MAXIBOT"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
PHASES = {"candidate", "preflight", "production"}
STATUSES = {
    "candidate": {"CI_PASS", "FAILED"},
    "preflight": {"READY", "FAILED"},
    "production": {"PLANNED", "COMPLETE", "FAILED_PRE_SWITCH", "RECOVERY_REQUIRED", "FAILED"},
}


def read_document(document_id: str, expected_title: str, api_key: str) -> str:
    query = "query ReadDocument($id: String!) { document(id: $id) { id title content } }"
    data = graphql_request(query, {"id": document_id}, api_key)
    document = data.get("document")
    if not isinstance(document, dict) or document.get("id") != document_id or document.get("title") != expected_title:
        raise SyncError("MAXIBOT Linear target document could not be verified")
    content = document.get("content")
    if not isinstance(content, str):
        raise SyncError("MAXIBOT Linear document content is unavailable")
    return content


def write_document(document_id: str, expected_title: str, content: str, api_key: str) -> None:
    mutation = """
      mutation UpdateDocument($id: String!, $input: DocumentUpdateInput!) {
        documentUpdate(id: $id, input: $input) { success document { id title } }
      }
    """
    data = graphql_request(mutation, {"id": document_id, "input": {"content": content}}, api_key)
    update = data.get("documentUpdate")
    document = update.get("document") if isinstance(update, dict) else None
    if not isinstance(update, dict) or update.get("success") is not True or not isinstance(document, dict):
        raise SyncError("MAXIBOT Linear document update was rejected")
    if document.get("id") != document_id or document.get("title") != expected_title:
        raise SyncError("MAXIBOT Linear update returned an unexpected document")
    if not _content_matches(content, read_document(document_id, expected_title, api_key)):
        raise SyncError("MAXIBOT Linear read-back does not match the published content")


def upsert_block(content: str, marker: str, block: str) -> str:
    start, end = f"<!-- {marker}_START -->", f"<!-- {marker}_END -->"
    starts, ends = content.count(start), content.count(end)
    if starts != ends or starts > 1:
        raise SyncError("MAXIBOT Linear automation markers are malformed")
    # Keep the closing HTML marker outside Markdown list continuation. Linear's
    # Markdown round-trip indents a comment immediately after a list item, which
    # makes exact read-back verification fail even though the document was saved.
    section = f"{start}\n{block.rstrip()}\n\n{end}"
    if starts == 1:
        before, rest = content.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + section + after
    return content.rstrip() + "\n\n" + section + "\n"


def event_markdown(phase: str, status: str, sha: str, migration: str, run_id: str, url: str, result_path: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"### {timestamp} — {phase}: **{status}**",
        "",
        f"* SHA: `{sha}`",
        f"* GitHub run: [#{run_id}]({url})",
    ]
    if phase != "candidate" and migration:
        lines.insert(4, f"* Target Alembic: `{migration}`")
    if phase == "production" and result_path:
        # Only publish an allowlist of non-secret machine facts from the result.
        try:
            import json
            from pathlib import Path
            result = json.loads(Path(result_path).read_text(encoding="utf-8"))
            for field, label in (("production_changed", "Изменение production"), ("preflight", "Preflight"),
                                 ("final_runtime_sha", "Production SHA после выкладки"),
                                 ("final_migration", "DB head после выкладки")):
                value = result.get(field)
                if isinstance(value, (str, bool)):
                    lines.append(f"* {label}: `{value}`")
        except (OSError, ValueError, TypeError):
            lines.append("* Детали результата: смотрите GitHub run и его result artifact.")
    return "\n".join(lines)


def publish(env: dict[str, str]) -> None:
    api_key = env.get("MAXIBOT_LINEAR_API_KEY", "") or env.get("LINEAR_API_KEY", "")
    phase = env.get("RELEASE_PHASE", "")
    status = env.get("RELEASE_STATUS", "")
    sha = env.get("CANDIDATE_SHA", "")
    migration = env.get("TARGET_MIGRATION", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = env.get("GITHUB_REPOSITORY", "akhripunov/maxibot")
    result_path = env.get("RELEASE_RESULT", "")
    if not api_key:
        raise SyncError("LINEAR_API_KEY repository secret is required")
    if phase not in PHASES or status not in STATUSES.get(phase, set()):
        raise SyncError("MAXIBOT release phase/status is invalid")
    if not SHA_RE.fullmatch(sha) or not run_id.isdigit():
        raise SyncError("MAXIBOT release identity is invalid")
    if phase != "candidate" and not MIGRATION_RE.fullmatch(migration):
        raise SyncError("MAXIBOT release identity is invalid")
    url = f"{server}/{repository}/actions/runs/{run_id}"
    block = event_markdown(phase, status, sha, migration, run_id, url, result_path)

    current_handoff = read_document(HANDOFF_ID, HANDOFF_TITLE, api_key)
    updated_handoff = upsert_block(current_handoff, f"MAXIBOT_RELEASE_{phase.upper()}", block)
    write_document(HANDOFF_ID, HANDOFF_TITLE, updated_handoff, api_key)

    current_journal = read_document(JOURNAL_ID, JOURNAL_TITLE, api_key)
    event_marker = f"<!-- MAXIBOT_RELEASE_EVENT_{run_id}_{phase}_{status} -->"
    if event_marker not in current_journal:
        updated_journal = current_journal.rstrip() + f"\n\n{event_marker}\n{block}\n"
        write_document(JOURNAL_ID, JOURNAL_TITLE, updated_journal, api_key)
    print(f"MAXIBOT_LINEAR_PUBLICATION=PASS PHASE={phase} STATUS={status} SHA={sha}")


def main() -> int:
    try:
        publish(dict(os.environ))
        return 0
    except SyncError as exc:
        print(f"MAXIBOT_LINEAR_PUBLICATION=FAIL REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
