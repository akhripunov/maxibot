#!/usr/bin/env python3
"""Publish bounded MAXIBOT release facts to the project Handoff and journal."""

from __future__ import annotations

import os
import base64
import io
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sync_agents_to_linear import SyncError, _content_matches, graphql_request
from release_assets import ReleaseAssetError, read_evidence, release_tag
from ghcr_release_artifacts import (EVIDENCE_ARTIFACT_TYPE, EVIDENCE_LAYER_MEDIA_TYPE,
    EVIDENCE_REPOSITORY, ArtifactError, MissingArtifactError, _registry_fetch,
    artifact_tag, resolve_oci_artifact)

HANDOFF_ID = "6f799a5c-75b2-4fc1-a522-86bab4c1950b"
HANDOFF_TITLE = "Архитектор — Handoff"
JOURNAL_ID = "13df4e43-f252-424c-afca-fdb2a37eaf29"
JOURNAL_TITLE = "Журнал релизов MAXIBOT"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
MIGRATION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
PHASES = {"candidate", "preflight", "production"}
STATUSES = {
    "candidate": {"CI_PASS", "FAILED", "CANCELLED", "SKIPPED"},
    "preflight": {"PASS", "READY", "READY_FOR_OWNER", "FAILED", "BLOCKED"},
    "production": {"COMPLETE", "FAILED_PRE_SWITCH", "RECOVERY_REQUIRED", "FAILED"},
}
REPOSITORY = "akhripunov/maxibot"
CURRENT_MARKER = "MAXIBOT_CURRENT_STATE"


def github_json(path: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SyncError("GITHUB_TOKEN is required to verify MAXIBOT current state")
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = urllib.request.Request(f"{base}/{path.lstrip('/')}", headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SyncError("GitHub resource not found") from None
        raise SyncError(f"GitHub current-state read failed: HTTP {exc.code}") from None
    except Exception as exc:
        raise SyncError(f"GitHub current-state read failed: {type(exc).__name__}") from exc


def read_result_artifact(run: dict) -> dict:
    sha = run.get("head_sha", "")
    run_id = str(run.get("id", ""))
    attempt = str(run.get("run_attempt", "1"))
    if SHA_RE.fullmatch(sha) and run_id.isdigit() and attempt.isdigit():
        tag = artifact_tag("evidence", sha, run_id, attempt)
        try:
            token = os.environ.get("GITHUB_TOKEN", "")
            digest, payload = resolve_oci_artifact(EVIDENCE_REPOSITORY, tag,
                EVIDENCE_ARTIFACT_TYPE, EVIDENCE_LAYER_MEDIA_TYPE,
                _registry_fetch(EVIDENCE_REPOSITORY, os.environ.get("GITHUB_ACTOR", ""), token))
            evidence = json.loads(payload.decode("utf-8"))
            if (evidence.get("schema") != "MAXIBOT_RELEASE_WORKFLOW_RESULT_V1"
                    or evidence.get("candidate_sha") != sha
                    or str(evidence.get("github_run_id")) != run_id
                    or str(evidence.get("github_run_attempt")) != attempt):
                raise SyncError("GHCR Deploy evidence identity does not match the exact workflow attempt")
            evidence["evidence_digest"] = digest
            return evidence
        except MissingArtifactError:
            # Historical Deploy runs predate OCI evidence; only a confirmed missing GHCR tag
            # may use the original Actions result ZIP fallback below.
            pass
        except (ArtifactError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SyncError("Immutable MAXIBOT GHCR Deploy evidence failed validation") from exc
    # V3 production runs persisted their canonical result inside an immutable
    # GitHub Release evidence.zip. Keep that verified path available while old
    # Actions result ZIPs age out; never downgrade from a present but invalid
    # GHCR or Release evidence object.
    if SHA_RE.fullmatch(sha) and run_id.isdigit() and attempt.isdigit():
        tag = release_tag("evidence", sha, run_id, attempt)
        try:
            github_json(f"repos/{REPOSITORY}/releases/tags/{tag}")
        except SyncError as exc:
            if str(exc) != "GitHub resource not found":
                raise
        else:
            token = os.environ.get("GITHUB_TOKEN", "")
            with tempfile.TemporaryDirectory(prefix="maxibot-legacy-evidence-") as directory:
                try:
                    read_evidence(sha, run_id, attempt, Path(directory),
                        lambda path: github_json(f"repos/{REPOSITORY}/{path}"), token)
                    return json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
                except (ReleaseAssetError, OSError, json.JSONDecodeError) as exc:
                    raise SyncError("Immutable historical MAXIBOT Release evidence failed validation") from exc
    artifacts = github_json(f"repos/{REPOSITORY}/actions/runs/{run['id']}/artifacts").get("artifacts", [])
    artifacts = [a for a in artifacts if a.get("name", "").startswith(f"maxibot-release-result-{run['id']}-") and not a.get("expired")]
    if len(artifacts) != 1:
        raise SyncError(f"Expected one MAXIBOT canonical result artifact for run {run['id']}; found {len(artifacts)}")
    request = urllib.request.Request(artifacts[0]["archive_download_url"], headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"})
    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # Artifact endpoints redirect to signed object storage URLs. Never
            # forward the GitHub bearer token to a different host.
            if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
                return urllib.request.Request(newurl, headers={"Accept": "application/octet-stream"})
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    try:
        with urllib.request.build_opener(SafeRedirect).open(request, timeout=30) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
            files = [name for name in archive.namelist() if name.endswith(".json")]
            if len(files) != 1:
                raise SyncError(f"Expected one JSON in MAXIBOT result artifact {run['id']}")
            return json.loads(archive.read(files[0]))
    except SyncError:
        raise
    except Exception as exc:
        raise SyncError(f"MAXIBOT canonical result artifact read failed: {type(exc).__name__}") from exc


def latest_runs(workflow: str) -> list[dict]:
    return github_json(f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs?branch=main&per_page=100").get("workflow_runs", [])


def release_jobs(run: dict) -> dict[str, dict]:
    run_id = str(run.get("id", ""))
    attempt = str(run.get("run_attempt", "1"))
    if not run_id.isdigit() or not attempt.isdigit():
        return {}
    jobs = github_json(
        f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    ).get("jobs", [])
    return {job.get("name"): job for job in jobs if isinstance(job, dict) and isinstance(job.get("name"), str)}


def trusted_production_run(run: dict, jobs: dict[str, dict]) -> bool:
    if any(jobs.get(name, {}).get("status") != "completed"
           or jobs.get(name, {}).get("conclusion") != "success"
           for name in ("validate", "deploy")):
        return False
    publish = jobs.get("publish", {})
    if run.get("status") == "in_progress":
        return publish.get("status") == "in_progress"
    if run.get("status") != "completed":
        return False
    if run.get("conclusion") == "success":
        return publish.get("status") == "completed" and publish.get("conclusion") == "success"
    return (run.get("conclusion") == "failure" and publish.get("status") == "completed"
            and publish.get("conclusion") == "failure")


def current_candidate_status(main_sha: str, current_main_run: dict | None, event: dict | None = None) -> str:
    if current_main_run and current_main_run.get("status") == "completed" and current_main_run.get("conclusion") == "success":
        return "READY"
    if (event and event.get("sha") == main_sha and current_main_run and
            str(current_main_run.get("id")) == str(event.get("run_id")) and event.get("status") == "CI_PASS"):
        # Prepare job completed successfully (needs.prepare.result=success), but
        # the containing workflow is still running its Linear publisher job.
        return "PREPARE_GATES_PASS_WORKFLOW_PENDING"
    return "NOT_PREPARED_FOR_CURRENT_MAIN"


def current_state(event: dict | None = None) -> dict:
    commit = github_json(f"repos/{REPOSITORY}/commits/main")
    main_sha = commit.get("sha", "")
    if not SHA_RE.fullmatch(main_sha):
        raise SyncError("GitHub main SHA is invalid")
    prepare_runs = latest_runs("maxibot-prepare.yml")
    successful_prepares = [r for r in prepare_runs if r.get("status") == "completed" and r.get("conclusion") == "success"]
    last_prepare = max(successful_prepares, key=lambda r: r.get("created_at", ""), default=None)
    current_prepare = max((r for r in prepare_runs if r.get("head_sha") == main_sha),
                          key=lambda r: (r.get("created_at", ""), r.get("run_attempt", 0), r.get("id", 0)), default=None)
    deploy_runs = latest_runs("maxibot-deploy.yml")
    production_runs = sorted((r for r in deploy_runs if
                              (r.get("conclusion") == "success" or r.get("status") == "in_progress"
                               or (r.get("status") == "completed" and r.get("conclusion") == "failure")) and
                              any(marker in (r.get("name") or r.get("display_title") or "").lower()
                                  for marker in ("production ", "task-scoped-auto ", "publication-recovery "))),
                             key=lambda r: r.get("created_at", ""), reverse=True)
    deployment = evidence = None
    for run in production_runs:
        jobs = release_jobs(run)
        if not trusted_production_run(run, jobs):
            continue
        result = read_result_artifact(run)
        mode = result.get("mode")
        is_reconciliation = mode == "publication-recovery"
        if ((mode in {"production", "task-scoped-auto"} or is_reconciliation)
                and result.get("status") == "COMPLETE"):
            if (str(result.get("github_run_id")) != str(run.get("id"))
                    or str(result.get("github_run_attempt")) != str(run.get("run_attempt", "1"))):
                raise SyncError("MAXIBOT production result artifact run identity mismatch")
            if result.get("final_runtime_sha") != result.get("candidate_sha"):
                raise SyncError("MAXIBOT production result artifact SHA mismatch")
            if result.get("candidate_sha") != run.get("head_sha"):
                raise SyncError("MAXIBOT production result artifact does not match its workflow head SHA")
            is_publisher_recovery = run.get("status") == "in_progress" or run.get("conclusion") == "failure"
            if is_publisher_recovery and not DIGEST_RE.fullmatch(str(result.get("evidence_digest", ""))):
                raise SyncError("MAXIBOT publisher recovery requires exact immutable Deploy evidence")
            deployment_id = result.get("github_deployment_id")
            if deployment_id is None and not is_publisher_recovery:  # Historical runs predate V5 deployment tracking.
                result["deployment_status"] = "LEGACY_NOT_TRACKED"
            else:
                if not isinstance(deployment_id, int) or deployment_id <= 0:
                    raise SyncError("MAXIBOT production result has an invalid GitHub Deployment identity")
                deployment_record = github_json(f"repos/{REPOSITORY}/deployments/{deployment_id}")
                statuses = github_json(f"repos/{REPOSITORY}/deployments/{deployment_id}/statuses?per_page=100")
                payload = deployment_record.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = None
                linked_run_id = result.get("reconciles_deploy_run_id") if is_reconciliation else run.get("id")
                linked_attempt = (result.get("reconciles_deploy_run_attempt") if is_reconciliation
                                  else run.get("run_attempt", "1"))
                if (deployment_record.get("sha") != result.get("candidate_sha")
                        or deployment_record.get("environment") != "maxibot-production"
                        or not isinstance(payload, dict)
                        or str(payload.get("workflow_run_id")) != str(linked_run_id)
                        or str(payload.get("workflow_run_attempt")) != str(linked_attempt)
                        or not isinstance(statuses, list) or not statuses
                        or not isinstance(statuses[0], dict)
                        or statuses[0].get("state") != "success"):
                    raise SyncError("MAXIBOT GitHub Deployment readback is not a successful exact-SHA release")
                result["deployment_status"] = statuses[0].get("state")
            if is_reconciliation and (
                    result.get("production_changed") is not False
                    or result.get("recovery") != "READ_ONLY_RECONCILIATION_PASS"
                    or not str(result.get("reconciles_deploy_run_id", "")).isdigit()
                    or not str(result.get("reconciles_deploy_run_attempt", "")).isdigit()):
                raise SyncError("MAXIBOT read-only production reconciliation evidence is incomplete")
            deployment, evidence = run, result
            break
    if deployment is None:
        raise SyncError("No successful MAXIBOT production result artifact could be verified")
    prod_sha = evidence.get("final_runtime_sha")
    if not SHA_RE.fullmatch(prod_sha or ""):
        raise SyncError("MAXIBOT production SHA is invalid")
    return {
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "main_sha": main_sha,
        "main_checked_at": commit.get("commit", {}).get("committer", {}).get("date", "unknown"),
        "production_sha": prod_sha, "production_checked_at": deployment.get("updated_at", deployment.get("created_at", "unknown")),
        "production_status": evidence.get("status"), "in_sync": main_sha == prod_sha,
        "production_changed": evidence.get(
            "production_changed", evidence.get("mode") in {"production", "task-scoped-auto"}
        ),
        "reconciliation": evidence.get("mode") == "publication-recovery",
        "reconciles_deploy_run_id": evidence.get("reconciles_deploy_run_id"),
        "reconciles_deploy_run_attempt": evidence.get("reconciles_deploy_run_attempt"),
        "prepare": last_prepare, "current_prepare": current_prepare,
        "candidate": current_candidate_status(main_sha, current_prepare, event),
        "deploy": deployment, "evidence": evidence,
    }


def render_current_state(state: dict) -> str:
    prepare, deploy, evidence = state["prepare"] or {}, state["deploy"], state["evidence"]
    relation = "IN_SYNC" if state["in_sync"] else "MAIN_AHEAD_OF_PRODUCTION"
    timestamp = state["production_checked_at"]
    migration = evidence.get("final_migration") or evidence.get("target_migration") or "unconfirmed"
    # COMPLETE is emitted only after post-deploy API readiness, Worker heartbeat/restart
    # observation, and disabled ADS write flags pass in the canonical release script.
    if state.get("reconciliation"):
        production_line = (
            f"Production: `{state['production_sha']}`; источник: read-only reconciliation run "
            f"[#{deploy['id']}]({deploy['html_url']}); original Deploy run "
            f"`#{state.get('reconciles_deploy_run_id')}` attempt `"
            f"{state.get('reconciles_deploy_run_attempt')}`; проверка завершена `{timestamp}`; "
            "canonical status **COMPLETE**, runtime не изменялся."
        )
        deploy_line = (
            f"Последний подтверждённый production state: `{state['production_sha']}` / "
            f"read-only reconciliation run `#{deploy['id']}` / **COMPLETE**."
        )
        evidence_description = "read-only reconciliation OCI evidence"
    else:
        production_line = (
            f"Production: `{state['production_sha']}`; source: verified MAXIBOT OCI evidence and "
            f"GitHub Deployment `#{evidence.get('github_deployment_id')}`; workflow run "
            f"[#{deploy['id']}]({deploy['html_url']}); проверка завершена `{timestamp}`; "
            f"canonical status **{state['production_status']}**."
        )
        deploy_line = (
            f"Последний production Deploy: `{state['production_sha']}` / run `#{deploy['id']}` / "
            f"**{state['production_status']}**."
        )
        evidence_description = "canonical production OCI evidence"
    lines = [
        f"Синхронизировано: `{state['synced_at']}` UTC.",
        f"GitHub main: `{state['main_sha']}` (commit time `{state['main_checked_at']}`).",
        production_line,
        f"Сверка SHA: **{relation}**.",
        f"Последний завершившийся успешный Prepare: `{prepare.get('head_sha', 'unknown')}` / run `#{prepare.get('id', 'unknown')}`; current-main run `#{(state['current_prepare'] or {}).get('id', 'unknown')}`: **{state['candidate']}**.",
        deploy_line,
        f"Alembic production head: `{migration}` ({evidence_description}).",
        f"Последнее подтверждённое API/Worker состояние: **PASS** в post-deploy checks, `{timestamp}`; это состояние на момент Deploy, не свежая независимая VPS-проверка.",
        f"ADS_AUTOPILOT_ENABLED: **FALSE**; ADS_WRITE_ACTIONS_ENABLED: **FALSE** (обязательные pre/post-deploy guards того же завершённого Deploy, `{timestamp}`).",
        "Пользовательская приёмка: **OWNER_ACCEPTANCE_NOT_RECORDED**; technical COMPLETE её не подтверждает.",
        "Следующее действие: подготовить/рассмотреть текущий main только в рамках отдельного разрешённого релиза; production здесь не менялся.",
    ]
    return f"<!-- {CURRENT_MARKER}_START -->\n\n" + "\n\n".join(lines) + f"\n\n<!-- {CURRENT_MARKER}_END -->"


def upsert_current_state(content: str, block: str, handoff: bool = False) -> str:
    start, end = f"<!-- {CURRENT_MARKER}_START -->", f"<!-- {CURRENT_MARKER}_END -->"
    starts, ends = content.count(start), content.count(end)
    # Linear may preserve a duplicated adjacent closing marker from an older
    # write. Repair only this unambiguous shape; all other marker mismatches
    # remain fail-closed.
    if starts == 1 and ends == 2 and end + end in content:
        content = content.replace(end + end, end, 1)
        ends = content.count(end)
    elif starts == 0 and ends == 2 and end + end in content:
        content = content.replace(end + end, "", 1)
        ends = 0
    if starts != ends or starts > 1:
        raise SyncError("MAXIBOT CURRENT STATE markers are malformed")
    if starts:
        before, rest = content.split(start, 1)
        _, after = rest.split(end, 1)
        return before + block + after
    if handoff:
        return block + "\n\n" + content
    title_end = content.find("\n")
    if not content.startswith("# ") or title_end < 0:
        return block + "\n\n" + content
    return content[:title_end + 1] + "\n" + block + "\n\n" + content[title_end + 1:].lstrip("\n")


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


def write_document(document_id: str, expected_title: str, content: str, api_key: str,
                   *, allow_trailing_newline_variation: bool = False) -> None:
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
    # Linear document writes can become visible to subsequent reads a few
    # seconds after the mutation response. Retry only the read; never repeat
    # the document update, and remain fail-closed after this bounded window.
    for attempt in range(4):
        returned = read_document(document_id, expected_title, api_key)
        matches = _content_matches(content, returned)
        if (not matches and allow_trailing_newline_variation
                and _content_matches(content.rstrip("\n"), returned.rstrip("\n"))):
            matches = True
        if matches:
            return
        if attempt < 3:
            time.sleep(0.5)
    raise SyncError("MAXIBOT Linear read-back does not match the published content")


def upsert_block(content: str, marker: str, block: str) -> str:
    start, end = f"<!-- {marker}_START -->", f"<!-- {marker}_END -->"
    starts, ends = content.count(start), content.count(end)
    if starts != ends or starts > 1:
        raise SyncError("MAXIBOT Linear automation markers are malformed")
    # Linear's Markdown round-trip inserts blank lines around HTML comments.
    # Keep both separators in our submitted content so exact read-back verification
    # succeeds instead of treating a successful save as a failed publication.
    section = f"{start}\n\n{block.rstrip()}\n\n{end}"
    if starts == 1:
        before, rest = content.split(start, 1)
        _, after = rest.split(end, 1)
        return before.rstrip() + "\n\n" + section + after
    return content.rstrip() + "\n\n" + section + "\n"


def append_journal_event(content: str, event_marker: str, block: str) -> str:
    if event_marker in content:
        return content
    # Linear's document Markdown round-trip separates HTML comments from
    # adjacent content. Submit the canonical spacing up front so strict
    # read-back verification compares equivalent content.
    return content.rstrip() + f"\n\n{event_marker}\n\n{block}\n"


def journal_event_marker(run_id: str, attempt: str, phase: str, status: str) -> str:
    return f"<!-- MAXIBOT_RELEASE_EVENT_{run_id}_ATTEMPT_{attempt}_{phase}_{status} -->"


def candidate_status(prepare_result: str) -> str:
    return {"success": "CI_PASS", "cancelled": "CANCELLED", "skipped": "SKIPPED"}.get(prepare_result, "FAILED")


def event_markdown(phase: str, status: str, sha: str, migration: str, run_id: str, attempt: str, url: str, result_path: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"### {timestamp} — {phase}: **{status}**",
        "",
        f"* SHA: `{sha}`",
        f"* GitHub run: [#{run_id}, попытка {attempt}]({url}/attempts/{attempt})",
    ]
    if int(attempt) > 1:
        lines.extend(["", "Повторный запуск: предыдущая запись сохранена; статус относится только к этой попытке."])
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
    if phase == "preflight" and status == "READY_FOR_OWNER":
        lines.extend([
            "",
            f"**MAXIBOT SHA {sha} полностью готов к production. Все mandatory gates PASS. "
            f"Target Alembic: {migration}. Разрешить production?**",
            "",
            "Approval должен подтвердить этот полный SHA и target; он запускает один canonical production workflow. "
            "Этот preflight не менял production.",
        ])
    elif phase == "preflight" and status == "PASS":
        lines.extend([
            "",
            "Read-only preflight прошёл внутри этого Deploy до Apply. Статус READY_FOR_OWNER не публикуется для уже выполняемого автоматического production цикла.",
        ])
    return "\n".join(lines)


def publish(env: dict[str, str], state: dict | None = None) -> None:
    api_key = env.get("MAXIBOT_LINEAR_API_KEY", "") or env.get("LINEAR_API_KEY", "")
    phase = env.get("RELEASE_PHASE", "")
    status = candidate_status(env.get("PREPARE_RESULT", "")) if phase == "candidate" else env.get("RELEASE_STATUS", "")
    sha = env.get("CANDIDATE_SHA", "")
    migration = env.get("TARGET_MIGRATION", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = env.get("GITHUB_REPOSITORY", "akhripunov/maxibot")
    result_path = env.get("RELEASE_RESULT", "")
    if not api_key:
        raise SyncError("LINEAR_API_KEY repository secret is required")
    if phase not in PHASES or status not in STATUSES.get(phase, set()):
        raise SyncError("MAXIBOT release phase/status is invalid")
    if not SHA_RE.fullmatch(sha) or not run_id.isdigit() or not attempt.isdigit() or int(attempt) < 1:
        raise SyncError("MAXIBOT release identity is invalid")
    if phase != "candidate" and not MIGRATION_RE.fullmatch(migration):
        raise SyncError("MAXIBOT release identity is invalid")
    url = f"{server}/{repository}/actions/runs/{run_id}"
    block = event_markdown(phase, status, sha, migration, run_id, attempt, url, result_path)
    metrics = ("GITHUB_HOSTED_JOB_COUNT", "GITHUB_HOSTED_RUNNER_SECONDS",
               "SELF_HOSTED_JOB_COUNT", "SELF_HOSTED_RUNNER_SECONDS",
               "DUPLICATE_HOSTED_WORK_REMOVED", "CACHE_PREVENTED_DOWNLOAD")
    if any(env.get(key) for key in metrics):
        block += "\n\n### Runner and artifact telemetry\n\n" + "\n".join(
            f"* {key}: `{env.get(key, 'UNKNOWN')}`" for key in metrics
        )
    state_block = render_current_state(state or current_state())

    current_handoff = read_document(HANDOFF_ID, HANDOFF_TITLE, api_key)
    updated_handoff = upsert_block(current_handoff, f"MAXIBOT_RELEASE_{phase.upper()}", block)
    updated_handoff = upsert_current_state(updated_handoff, state_block, handoff=True)
    write_document(HANDOFF_ID, HANDOFF_TITLE, updated_handoff, api_key)

    current_journal = read_document(JOURNAL_ID, JOURNAL_TITLE, api_key)
    event_marker = journal_event_marker(run_id, attempt, phase, status)
    updated_journal = append_journal_event(current_journal, event_marker, block)
    updated_journal = upsert_current_state(updated_journal, state_block)
    if updated_journal != current_journal:
        write_document(JOURNAL_ID, JOURNAL_TITLE, updated_journal, api_key,
                       allow_trailing_newline_variation=True)
    print(f"MAXIBOT_LINEAR_PUBLICATION=PASS PHASE={phase} STATUS={status} SHA={sha}")


def sync_current_state(api_key: str) -> None:
    if not api_key:
        raise SyncError("LINEAR_API_KEY repository secret is required")
    block = render_current_state(current_state())
    handoff = read_document(HANDOFF_ID, HANDOFF_TITLE, api_key)
    write_document(HANDOFF_ID, HANDOFF_TITLE, upsert_current_state(handoff, block, handoff=True), api_key)
    journal = read_document(JOURNAL_ID, JOURNAL_TITLE, api_key)
    updated = upsert_current_state(journal, block)
    if updated != journal:
        write_document(JOURNAL_ID, JOURNAL_TITLE, updated, api_key,
                       allow_trailing_newline_variation=True)


def main() -> int:
    try:
        env = dict(os.environ)
        if env.get("STATE_SYNC_ONLY") == "true":
            sync_current_state(env.get("MAXIBOT_LINEAR_API_KEY", "") or env.get("LINEAR_API_KEY", ""))
            print("MAXIBOT_CURRENT_STATE_SYNC=PASS")
            return 0
        phase = env.get("RELEASE_PHASE", "")
        event = {"sha": env.get("CANDIDATE_SHA", ""), "run_id": env.get("GITHUB_RUN_ID", ""),
                 "status": candidate_status(env.get("PREPARE_RESULT", ""))} if phase == "candidate" else None
        publish(env, state=current_state(event))
        return 0
    except SyncError as exc:
        print(f"MAXIBOT_LINEAR_PUBLICATION=FAIL REASON={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
