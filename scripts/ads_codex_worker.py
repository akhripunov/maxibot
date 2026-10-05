#!/usr/bin/env python3
"""Development-only bridge: internal API <-> local authenticated Codex CLI."""
import argparse, json, logging, os, signal, subprocess, tempfile, time, uuid, shutil
from pathlib import Path
import httpx

BASE=os.environ.get("ADS_CODEX_WORKER_API_BASE_URL","").rstrip("/")
TOKEN=os.environ.get("ADS_CODEX_WORKER_TOKEN","")
WORKER=os.environ.get("ADS_CODEX_WORKER_ID",f"mac-{uuid.uuid4().hex[:12]}")
RELEASE_ID=os.environ.get("ADS_CODEX_WORKER_RELEASE_ID", os.environ.get("MAXIBOT_RELEASE_ID", "unknown"))
POLL=int(os.environ.get("ADS_CODEX_WORKER_POLL_SECONDS","15")); TIMEOUT=int(os.environ.get("ADS_CODEX_EXECUTION_TIMEOUT_SECONDS","120")); STOP=False
logger = logging.getLogger("ads_codex_worker")

def halt(*_):
    global STOP; STOP=True
def headers(): return {"Authorization":f"Bearer {TOKEN}"}
logger.warning("ADS_CODEX_WORKER_START release_id=%s worker_id=%s python=%s", RELEASE_ID, WORKER, os.sys.version.split()[0])
def allowed_evidence_ids(safe_context):
    """Mirror the backend evidence registry; never invent semantic IDs."""
    facts = (safe_context or {}).get("FACTS") or {}
    v2 = facts.get("V2_ANALYSIS") or {}
    ids = set((facts.get("evidence") or {}).keys())
    ids.update((v2.get("evidence") or {}).keys())
    for trends in (facts.get("TRENDS") or {}, v2.get("trends") or {}):
        ids.update(item.get("trend_finding_id") for item in trends.get("findings") or [] if item.get("trend_finding_id"))
    if "MAXIBOT_QUALIFIED_MEMBERSHIP_RATE" in facts or "MAXIBOT_QUALIFIED_MEMBERSHIP_RATE" in v2:
        ids.add("MAXIBOT_QUALIFIED_MEMBERSHIP_RATE")
    ids.update((facts.get("DIRECTOLOGIST") or {}).get("evidence", {}).keys())
    return sorted(item for item in ids if isinstance(item, str))

def prompt(claim):
    safe_context = claim["safe_context"]
    allowed_ids = allowed_evidence_ids(safe_context)
    instructions = """Проанализируй ТОЛЬКО переданный контекст MaxiBot Ads и bounded V2_ANALYSIS dataset. UNKNOWN не равно ZERO. Campaign totals — единственный общий итог; не суммируй разные dimensions между собой. Используй deterministic findings как ranked candidates: strong wording только для SUFFICIENT, WEAK_SIGNAL означает только сигнал, INSUFFICIENT не даёт вывода. MAXIBOT_QUALIFIED_MEMBERSHIP_RATE означает только конверсию визит → подтверждённая подписка: qualified_memberships / tracked_landing_visits. Не называй её attribution coverage, attribution quality, подтверждением конверсий Яндекса или matching coverage. Campaign-specific matching обрабатывай отдельно и показывай UNKNOWN как недоступное сопоставление с конкретной кампанией. Разделяй Direct conversions и подтверждённые MaxiBot memberships как разные источники. Разделяй performance_confidence по данным Яндекса и confidence campaign-specific matching. Учитывай confidence, sample size и freshness. Ссылайся ТОЛЬКО на точные evidence IDs из списка ниже: копируй без изменений, не хешируй, не сокращай и не придумывай IDs. Если в списке нет подходящего evidence ID, не добавляй ссылку и явно укажи ограничение в missing_data. Если history недостаточна, явно укажи это и не выдумывай trend. Верни только JSON по campaign_diagnosis_schema_v1, без markdown. Поля summary, evidence.statement, recommendation.reason, do_not_change и memory_candidates.statement заполни на русском языке. diagnosis_code и recommendation.action_code оставь стабильными английскими кодами. Никогда не предлагай выполнение изменений или записи в Direct.

Разрешённые evidence IDs (копируй только их, без хеширования и генерации):
"""
    return instructions + json.dumps(allowed_ids, ensure_ascii=False) + "\nCONTEXT:\n" + json.dumps(safe_context, ensure_ascii=False)

def findings_grounded(claim):
    facts = (claim.get("safe_context") or {}).get("FACTS") or {}
    v2 = facts.get("V2_ANALYSIS") or {}
    allowed = set((facts.get("evidence") or {})) | set((v2.get("evidence") or {}))
    findings = v2.get("findings") or {}
    return all(ref in allowed for group in ("positive", "watch") for finding in findings.get(group, []) for ref in (finding.get("evidence_ids") or []))
def _safe_tail(value, limit=800):
    text = (value or "").replace("\n", " ").replace("\r", " ")
    return text[-limit:]

def _fail(client, claim, code, *, stage, started, exit_code=None, stdout=b"", stderr=b"", timeout=False, message=None):
    finished = time.time()
    diagnostics = {"stage": stage, "exception_class": message.split(":", 1)[0] if message else None, "safe_message": _safe_tail(message), "executable": shutil.which("codex"), "started_at": started, "finished_at": finished, "duration_ms": int((finished-started)*1000), "exit_code": exit_code, "stdout_bytes": len(stdout or b""), "stderr_bytes": len(stderr or b""), "structured_output_bytes": 0, "timeout": timeout, "output_produced": False, "stderr_tail": _safe_tail(stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr)}
    client.post(BASE+f"/internal/ads/codex/runs/{claim['run_id']}/fail",headers=headers(),json={"worker_id":WORKER,"lease_id":claim["lease_id"],"error_code":code,"retryable":False,"diagnostics":diagnostics})

def _telemetry(started, proc, output_bytes=0):
    finished=time.time()
    return {"stage":"codex_exec","executable":shutil.which("codex"),"exec_started_at":started,"exec_finished_at":finished,"exec_duration_ms":int((finished-started)*1000),"exit_code":proc.returncode,"stdout_bytes":len((proc.stdout or "").encode()),"stderr_bytes":len((proc.stderr or "").encode()),"structured_output_bytes":output_bytes,"timeout":False,"output_produced":bool(output_bytes)}

def once():
    if not BASE or not TOKEN: raise RuntimeError("WORKER_CONFIG_MISSING")
    with httpx.Client(timeout=20) as client:
        response=client.post(BASE+"/internal/ads/codex/claim",headers=headers(),json={"worker_id":WORKER,"worker_release_id":RELEASE_ID})
        if response.status_code==204:return False
        response.raise_for_status(); claim=response.json()
        if not findings_grounded(claim):
            _fail(client, claim, "CODEX_EVIDENCE_VALIDATION_FAILED", stage="finding_grounding", started=time.time(), message="EVIDENCE_NOT_IN_CONTEXT")
            return True
        with tempfile.TemporaryDirectory(prefix="maxibot-codex-") as directory:
            schema=Path(directory)/"schema.json"; out=Path(directory)/"result.json"
            schema_source=Path(__file__).resolve().parents[1]/"schemas/campaign_diagnosis_schema_v1.json"
            schema.write_text(schema_source.read_text(encoding="utf-8"), encoding="utf-8")
            started=time.time()
            if not shutil.which("codex"):
                _fail(client, claim, "CODEX_BINARY_NOT_FOUND", stage="preflight", started=started, message="FileNotFoundError: codex")
            else:
                try:
                    proc=subprocess.run(["codex","exec","--ephemeral","--skip-git-repo-check","--output-schema",str(schema),"--output-last-message",str(out),"-"],input=prompt(claim),text=True,cwd=directory,timeout=TIMEOUT,check=False,capture_output=True)
                    if proc.returncode != 0:
                        code="CODEX_AUTH_FAILED" if any(x in (proc.stderr or "").lower() for x in ("login", "auth", "unauthorized")) else "CODEX_EXEC_NONZERO"
                        _fail(client, claim, code, stage="codex_exec", started=started, exit_code=proc.returncode, stdout=proc.stdout.encode(), stderr=proc.stderr.encode(), message=f"ProcessError: exit {proc.returncode}")
                    elif not out.exists() or not out.read_text().strip():
                        _fail(client, claim, "CODEX_EMPTY_OUTPUT", stage="output", started=started, exit_code=proc.returncode, stdout=proc.stdout.encode(), stderr=proc.stderr.encode(), message="EmptyOutput")
                    else:
                        raw=out.read_text(); result=json.loads(raw)
                        response=client.post(BASE+f"/internal/ads/codex/runs/{claim['run_id']}/result",headers=headers(),json={"worker_id":WORKER,"lease_id":claim["lease_id"],"result":result,"telemetry":_telemetry(started,proc,len(raw.encode()))})
                        body=response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                        if response.status_code >= 500:
                            _fail(client, claim, "CODEX_RESULT_UPLOAD_FAILED", stage="result_upload", started=started, exit_code=proc.returncode, stdout=proc.stdout.encode(), stderr=proc.stderr.encode(), message=f"HTTPStatusError: {response.status_code}")
                        elif response.status_code >= 400:
                            _fail(client, claim, "CODEX_SCHEMA_VALIDATION_FAILED", stage="backend_validation", started=started, exit_code=proc.returncode, stdout=proc.stdout.encode(), stderr=proc.stderr.encode(), message=f"HTTPStatusError: {response.status_code}")
                        elif body.get("status") == "CODEX_FAILED":
                            validation_error = body.get("validation_error") or "validation failed"
                            code = "CODEX_EVIDENCE_VALIDATION_FAILED" if "evidence" in validation_error.lower() else "CODEX_SCHEMA_VALIDATION_FAILED"
                            _fail(client, claim, code, stage="backend_validation", started=started, exit_code=proc.returncode, stdout=proc.stdout.encode(), stderr=proc.stderr.encode(), message=f"ValidationError: {validation_error}")
                except subprocess.TimeoutExpired as exc:
                    _fail(client, claim, "CODEX_EXEC_TIMEOUT", stage="codex_exec", started=started, stdout=(exc.stdout or b""), stderr=(exc.stderr or b""), timeout=True, message="TimeoutExpired")
                except json.JSONDecodeError as exc:
                    _fail(client, claim, "CODEX_JSON_PARSE_FAILED", stage="json_parse", started=started, exit_code=proc.returncode, stdout=proc.stdout.encode(), stderr=proc.stderr.encode(), message=f"JSONDecodeError: {exc.msg}")
                except Exception as exc:
                    _fail(client, claim, "CODEX_UNKNOWN_RUNTIME_ERROR", stage="runtime", started=started, message=f"{type(exc).__name__}: {exc}")
    return True
if __name__=="__main__":
    parser=argparse.ArgumentParser(); group=parser.add_mutually_exclusive_group(required=True); group.add_argument("--once",action="store_true");group.add_argument("--watch",action="store_true"); args=parser.parse_args();signal.signal(signal.SIGINT,halt);signal.signal(signal.SIGTERM,halt)
    while not STOP:
        found=once()
        if args.once:break
        if not found:time.sleep(POLL)
