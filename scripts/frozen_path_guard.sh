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
        return "AdsPromotionCampaignLink" in text and not any(marker in text for marker in frozen_markers)
    if path.startswith("alembic/versions/"):
        text = changed_lines(path)
        frozen_markers = (
            "traffic_visits", "bot_traffic_visits", "max_identity_links",
            "bot_max_identity_links", "bot_started", "max_subscriber",
            "metrika", "paid_dedupe",
        )
        return "ads_promotion_campaign_links" in text and not any(marker in text.lower() for marker in frozen_markers)
    return False

bot_scoped = {
    path for path in changed
    if any(path == scoped_path and marker in diff_for(path) for scoped_path, marker in scoped_bot_only)
}
bot_direct = [p for p in changed if any(matches(x, p) for x in lists.get("bot_direct", []))]
channel_direct = [p for p in changed if any(matches(x, p) for x in lists.get("channel_direct", []))]
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
