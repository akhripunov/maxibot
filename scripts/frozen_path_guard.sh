#!/usr/bin/env bash
set -euo pipefail

repo="."
manifest="docs/frozen_paths.yaml"
base=""
head="HEAD"
owner_task=""
declare -a approved_paths=()

usage() {
  cat >&2 <<'EOF'
usage: frozen_path_guard.sh [--repo PATH] [--manifest PATH] [--base REV]
       [--head REV] [--owner-approved-task ID] [--owner-approved-path PATH]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --manifest) manifest="$2"; shift 2 ;;
    --base) base="$2"; shift 2 ;;
    --head) head="$2"; shift 2 ;;
    --owner-approved-task) owner_task="$2"; shift 2 ;;
    --owner-approved-path) approved_paths+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

if [[ -z "$owner_task" && ${#approved_paths[@]} -gt 0 ]]; then
  echo "OWNER_FROZEN_CHANGE_APPROVAL=NO"
  echo "FROZEN_PATH_GUARD=BLOCK"
  echo "REASON=owner-approved-path-requires-owner-approved-task" >&2
  exit 1
fi

if [[ -z "$base" ]]; then
  base="$(git -C "$repo" rev-parse "$head^")"
fi

python3 - "$repo" "$manifest" "$base" "$head" "$owner_task" "${approved_paths[@]-}" <<'PY'
from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

repo, manifest, base, head, owner_task, *approved = sys.argv[1:]
manifest_path = Path(manifest)
if not manifest_path.is_absolute():
    manifest_path = Path(repo) / manifest_path
text = manifest_path.read_text(encoding="utf-8")
lists: dict[str, list[str]] = {}
scoped_bot_only: list[tuple[str, str]] = []
in_paths = False
current = None
in_scoped = False
for line in text.splitlines():
    if line == "scoped_bot_only:":
        in_scoped = True
        in_paths = False
        current = None
        continue
    if in_scoped and line and not line.startswith(" "):
        in_scoped = False
    if in_scoped:
        match = re.match(r"^  - ([^|]+)\|(.+)$", line)
        if match:
            scoped_bot_only.append((match.group(1).strip(), match.group(2)))
        continue
    if line == "protected_runtime_areas:":
        in_paths = True
        continue
    if in_paths and line and not line.startswith(" "):
        in_paths = False
        current = None
    if not in_paths:
        continue
    match = re.match(r"^  ([a-z0-9_]+):\s*$", line)
    if match:
        current = match.group(1)
        lists[current] = []
        continue
    match = re.match(r"^    - (.+)$", line)
    if match and current:
        lists[current].append(match.group(1).strip())

def matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if "*" in pattern or "?" in pattern:
        return fnmatch.fnmatch(path, pattern)
    return path == pattern

changed = subprocess.check_output(
    ["git", "-C", repo, "diff", "--name-only", f"{base}..{head}"], text=True
).splitlines()
def diff_for(path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", repo, "diff", "--unified=0", f"{base}..{head}", "--", path], text=True
    )

def changed_lines(path: str) -> str:
    return "\n".join(
        line[1:]
        for line in diff_for(path).splitlines()
        if line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
    )

def ads_only_shared_change(path: str) -> bool:
    """Exempt only provider-neutral ADS schema plumbing from shared-path flags."""
    if path == "app/db/models.py":
        text = changed_lines(path)
        frozen_markers = (
            "TrafficVisit", "MaxIdentityLink", "BotMaxIdentityLink",
            "user_added", "max_subscriber", "bot_started", "Metrika",
            "paid_dedupe", "cross_object",
        )
        ads_markers = ("AdsAccount", "AdsCampaign", "AdsEntity", "AdsCreative", "AdsObservation", "AdsCollectionRun", "AdsAnalysisSnapshot", "AdsAiOutcome", "AdsPromotionCampaignLink", "AdsAction", "AdsExperiment", "snapshot_id", "SET NULL", "evaluation_status", "implementation_confirmed", "implementation_evidence_json", "baseline_metrics_json", "expected_direction", "actual_metrics_json", "reason_json", "problem_json", "root_cause_json", "expected_impact_json", "evaluation_window_days", "promotion_object_type", "promotion_object_id")
        return any(marker in text for marker in ads_markers) and not any(marker in text for marker in frozen_markers)
    if path == "app/core/config.py":
        text = changed_lines(path)
        allowed_names = (
            "ads_observations_retention_days",
            "ads_operational_retention_days",
            "ads_data_health_retention_days",
            "ads_retention_enabled",
            "ads_retention_batch_size",
            "ads_retention_interval_minutes",
            "ads_hourly_retention_days",
            "ads_daily_retention_days",
        )
        allowed_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "@property":
                allowed_lines.append(stripped)
                continue
            if stripped in {
                '"""Backward-compatible name for the canonical observations policy."""',
                '"""Daily aggregates are long-term; zero means no expiry."""',
            }:
                allowed_lines.append(stripped)
                continue
            if stripped in {
                "return 0",
                "return self.ads_observations_retention_days",
                "def ads_hourly_retention_days(self) -> int:",
                "def ads_daily_retention_days(self) -> int:",
            }:
                allowed_lines.append(stripped)
                continue
            if any(re.match(rf"{re.escape(name)}\b", stripped) for name in allowed_names):
                allowed_lines.append(stripped)
                continue
            return False
        return bool(allowed_lines) and any(name in text for name in allowed_names)
    if path.startswith("alembic/versions/"):
        text = changed_lines(path)
        frozen_markers = (
            "traffic_visits", "bot_traffic_visits", "max_identity_links",
            "bot_max_identity_links", "bot_started", "max_subscriber",
            "metrika", "paid_dedupe",
        )
        table_names = set(re.findall(r'"(ads_[a-z0-9_]+)"', text.lower()))
        allowed_tables = {"ads_recommendations", "ads_ai_outcomes", "ads_observations", "ads_collection_runs", "ads_report_runs"}
        return bool(table_names) and table_names <= allowed_tables and not any(marker in text.lower() for marker in frozen_markers)
    return False

def ads_only_channel_ui_change(path: str) -> bool:
    """Allow ADS storage labels in the shared menu without exempting flow code."""
    if path != "app/bot/menu.py":
        return False
    text = changed_lines(path)
    if not text or not any(marker in text for marker in ("ads_", "AdsAi", "AdsV2")):
        return False
    frozen_markers = (
        "TrafficVisit", "BotTrafficVisit", "MaxIdentityLink", "BotMaxIdentityLink",
        "user_added", "max_subscriber", "bot_started", "Metrika", "custom_bot",
    )
    return not any(marker in text for marker in frozen_markers)

bot_scoped = {
    path for path in changed
    if any(path == scoped_path and marker in diff_for(path) for scoped_path, marker in scoped_bot_only)
}
bot_direct = [p for p in changed if any(matches(x, p) for x in lists.get("bot_direct", []))]
channel_direct = [p for p in changed if any(matches(x, p) for x in lists.get("channel_direct", [])) and not ads_only_channel_ui_change(p)]
shared = [p for p in changed if any(matches(x, p) for x in lists.get("shared_dependencies", []))]
shared_for_impact = [p for p in shared if p not in bot_scoped and not ads_only_shared_change(p)]
protected = sorted(set(bot_direct + channel_direct + shared_for_impact))
approval_paths = [p for p in protected if owner_task and any(matches(a, p) for a in approved)]
approval_complete = bool(protected) and len(approval_paths) == len(protected)

def state(direct: list[str], shared_hit: bool) -> str:
    if direct:
        return "YES"
    if shared_hit:
        return "POSSIBLE"
    return "NO"

print(f"BOT_FROZEN_PATH_CHANGED={state(bot_direct, bool(shared_for_impact))}")
print(f"CHANNEL_FROZEN_PATH_CHANGED={state(channel_direct, bool(shared_for_impact))}")
print(f"OWNER_FROZEN_CHANGE_APPROVAL={'YES' if approval_complete else 'NO'}")
if not protected or approval_complete:
    print("FROZEN_PATH_GUARD=PASS")
    raise SystemExit(0)
print("FROZEN_PATH_GUARD=BLOCK")
raise SystemExit(1)
PY
