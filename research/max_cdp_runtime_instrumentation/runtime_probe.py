"""Capture the frontend object used to render a MAX Web join request.

This is intentionally a research-only, fail-closed probe. It connects to an
already authenticated browser supplied by the operator through CDP. It never
reads cookies, localStorage, sessionStorage, browser profiles, or credentials;
it does not click, approve, reject, or send WebSocket messages.

Usage (operator must provide a CDP endpoint):
    MAX_CDP_ENDPOINT=http://127.0.0.1:9222 \
      python research/max_cdp_runtime_instrumentation/runtime_probe.py

The browser page must already show the pending-request UI. Evidence is written
to ``research/max_cdp_runtime_instrumentation/evidence.jsonl`` unless changed
with ``MAX_RUNTIME_EVIDENCE_PATH``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import Page, sync_playwright
except ImportError:  # pragma: no cover - exercised by manual-run environment
    Page = Any  # type: ignore[misc,assignment]
    sync_playwright = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent
EVIDENCE_PATH = Path(os.environ.get("MAX_RUNTIME_EVIDENCE_PATH", ROOT / "evidence.jsonl"))
NAME = "Любимая Печенька"


def _redact_key(key: str) -> bool:
    return bool(re.search(r"token|cookie|authorization|secret|password|session|credential", key, re.I))


def sanitize(value: Any, *, depth: int = 0) -> Any:
    """Keep only bounded JSON-safe evidence and redact secret-like keys."""
    if depth > 8:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 500:
            return value[:500] + "...[TRUNCATED]"
        return value
    if isinstance(value, list):
        return [sanitize(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:200]:
            key = str(raw_key)
            if _redact_key(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = sanitize(raw_value, depth=depth + 1)
        return result
    return f"[{type(value).__name__}]"


def is_relevant(value: Any) -> bool:
    """Conservative relevance filter: never persist arbitrary page state."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    markers = ("JOIN_REQUEST", "pendingJoinRequest", NAME, "contact.id")
    return any(marker in text for marker in markers)


def identity_verified(value: Any) -> bool:
    """Require all identity dimensions in one decoded candidate object."""
    text = json.dumps(value, ensure_ascii=False, default=str).lower()
    has_name = NAME.lower() in text or "displayname" in text
    has_join = "join_request" in text or "pendingjoinrequest" in text
    has_chat = "chatid" in text
    has_user = bool(re.search(r'"(?:contact\.id|userid|user_id)"\s*:', text))
    return has_name and has_join and has_chat and has_user


def _write(event: str, payload: Any) -> None:
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "payload": sanitize(payload),
    }
    with EVIDENCE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


INSTRUMENTATION_JS = r"""
(() => {
  const marker = "JOIN_REQUEST";
  const name = "Любимая Печенька";
  const out = [];
  const seen = new WeakSet();
  const secret = /token|cookie|authorization|secret|password|session|credential/i;
  const relevant = (v) => {
    try { const s = JSON.stringify(v); return s.includes(marker) || s.includes(name) || s.includes("pendingJoinRequest") || s.includes("displayName") || s.includes("contact.id"); }
    catch (_) { return false; }
  };
  const safe = (v, depth = 0) => {
    if (depth > 7 || v === null || typeof v !== "object") return typeof v === "string" && v.length > 500 ? v.slice(0,500) + "...[TRUNCATED]" : v;
    if (seen.has(v)) return "[CYCLE]";
    seen.add(v);
    if (Array.isArray(v)) return v.slice(0,100).map(x => safe(x, depth + 1));
    const o = {};
    Object.keys(v).slice(0,200).forEach(k => { o[k] = secret.test(k) ? "[REDACTED]" : safe(v[k], depth + 1); });
    return o;
  };
  const record = (source, value) => { if (relevant(value)) out.push({source, value: safe(value)}); };
  window.__MAXIBOT_RUNTIME_EVIDENCE__ = out;
  window.__MAXIBOT_RECORD_RUNTIME__ = record;

  // Capture decoded text/JSON responses only; binary payloads are excluded.
  const OriginalWebSocket = window.WebSocket;
  window.WebSocket = function(...args) {
    const ws = new OriginalWebSocket(...args);
    ws.addEventListener("message", event => {
      if (typeof event.data === "string") { try { const parsed = JSON.parse(event.data); record("websocket-json", parsed); } catch (_) {} }
    });
    return ws;
  };
  window.WebSocket.prototype = OriginalWebSocket.prototype;
  window.WebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
  window.WebSocket.OPEN = OriginalWebSocket.OPEN;
  window.WebSocket.CLOSING = OriginalWebSocket.CLOSING;
  window.WebSocket.CLOSED = OriginalWebSocket.CLOSED;

  const originalFetch = window.fetch;
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try { const clone = response.clone(); const text = await clone.text(); if (text.length < 100000) { const parsed = JSON.parse(text); record("fetch-json", parsed); } } catch (_) {}
    return response;
  };

  const scan = () => {
    document.querySelectorAll("*[data-testid], [role], button, li, tr, div").forEach(node => {
      const text = (node.innerText || "").trim();
      if (text.includes(name)) {
        const item = {text: text.slice(0,500), tag: node.tagName, testid: node.getAttribute("data-testid"), role: node.getAttribute("role")};
        for (const key of Object.keys(node)) {
          if (key.startsWith("__reactProps") || key.startsWith("__reactFiber")) {
            try { record("dom-react-" + key.split("$")[0], node[key]); } catch (_) {}
          }
        }
        record("dom-visible-candidate", item);
      }
    });
  };
  scan();
  new MutationObserver(scan).observe(document.documentElement, {subtree:true, childList:true, characterData:true});
})();
"""


def collect_page(page: Page) -> int:
    page.evaluate(INSTRUMENTATION_JS)
    page.wait_for_timeout(1500)
    records = page.evaluate("window.__MAXIBOT_RUNTIME_EVIDENCE__ || []")
    count = 0
    for record in records:
        if is_relevant(record):
            _write("runtime-candidate", record)
            count += 1
    _write("page-meta", {"url_pattern": re.sub(r"https?://[^/]+", "[ORIGIN]", page.url), "title": page.title()[:200]})
    return count


def main() -> int:
    endpoint = os.environ.get("MAX_CDP_ENDPOINT")
    if not endpoint:
        print("BLOCKED: set MAX_CDP_ENDPOINT to a manually supplied CDP endpoint", file=sys.stderr)
        return 2
    if sync_playwright is None:
        print("BLOCKED: install Playwright in the research environment", file=sys.stderr)
        return 2
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        pages = [page for context in browser.contexts for page in context.pages]
        max_pages = [page for page in pages if "max" in page.url.lower() or "oneme" in page.url.lower()]
        if not max_pages:
            print("BLOCKED: no MAX Web page found in the supplied CDP session", file=sys.stderr)
            return 3
        _write("capture-start", {"pages": len(max_pages), "actions": ["runtime instrumentation", "read-only DOM inspection"]})
        total = sum(collect_page(page) for page in max_pages)
        verified = any(identity_verified(record) for record in records)
        _write("verdict", {"display_name": NAME, "pending_user_id_verified": "YES" if verified else "NO", "candidate_count": total, "reason": "A positive result requires one relevant object containing displayName, chatId, JOIN_REQUEST, and contact.id/userId."})
        print(json.dumps({"evidence": str(EVIDENCE_PATH), "candidate_count": total, "PENDING_USER_ID_VERIFIED": "YES" if verified else "NO"}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
