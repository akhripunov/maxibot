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
        creative_lines = {
            'source_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)',
            'storage_provider: Mapped[Optional[str]] = mapped_column(String(32))',
            'storage_key: Mapped[Optional[str]] = mapped_column(String(1024), index=True)',
            'media_storage_state: Mapped[Optional[str]] = mapped_column(String(32), index=True)',
            'mime_type: Mapped[Optional[str]] = mapped_column(String(128))',
            'file_size: Mapped[Optional[int]] = mapped_column(BigInteger)',
            'width: Mapped[Optional[int]] = mapped_column(Integer)',
            'height: Mapped[Optional[int]] = mapped_column(Integer)',
            'duration_seconds: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))',
            'ai_features_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)',
            'last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)',
            'provider_state: Mapped[Optional[str]] = mapped_column(String(64))',
        }
        changed_lines_set = {line.strip() for line in text.splitlines() if line.strip()}
        creative_only = bool(changed_lines_set) and changed_lines_set <= creative_lines
        return (any(marker in text for marker in ads_markers) or creative_only) and not any(marker in text for marker in frozen_markers)
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
            "mail_cloud_enabled",
            "mail_cloud_webdav_url",
            "mail_cloud_login",
            "mail_cloud_app_password",
            "mail_cloud_creative_root",
            "mail_cloud_max_media_bytes",
            "mail_cloud_timeout_seconds",
            "mail_cloud_retry_attempts",
            "mail_cloud_retry_backoff_seconds",
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
        if path == "alembic/versions/0075a_alembic_version_width.py":
            allowed_lines = {
                '"""Widen Alembic\'s version column before long ADS revisions are recorded."""',
                "from alembic import op",
                "import sqlalchemy as sa",
                'revision = "0075a_alembic_version_width"',
                'down_revision = "0075_ads_canonical_v2"',
                "branch_labels = None",
                "depends_on = None",
                "def upgrade() -> None:",
                "def downgrade() -> None:",
                "op.alter_column(",
                '"alembic_version",',
                '"version_num",',
                "existing_type=sa.String(length=32),",
                "type_=sa.String(length=64),",
                "existing_nullable=False,",
                "existing_type=sa.String(length=64),",
                "type_=sa.String(length=32),",
                ")",
            }
            changed = [line.strip() for line in text.splitlines() if line.strip()]
            return (
                changed.count("op.alter_column(") == 2
                and changed.count(")") == 2
                and all(line in allowed_lines for line in changed)
                and '"alembic_version",' in changed
                and '"version_num",' in changed
            )
        if path == "alembic/versions/0076_ads_directologist_decision_engine_v1.py":
            changed = [line.strip() for line in text.splitlines() if line.strip()]
            return changed == [
                'down_revision = "0075_ads_canonical_v2"',
                'down_revision = "0075a_alembic_version_width"',
            ]
        if path == "alembic/versions/0079_ads_objective_target_cpa_v1.py":
            # Exact, fail-closed exception for the Objective V1 migration.
            # Do not turn this into a filename/prefix allowlist: every
            # operation and every typed column must match the approved
            # promotion-link-only schema delta.
            normalized = re.sub(r"\s+", " ", text).strip()
            expected_adds = (
                'op.add_column("ads_promotion_campaign_links", sa.Column("objective_type", sa.String(length=32), nullable=True))',
                'op.add_column("ads_promotion_campaign_links", sa.Column("target_cpa", sa.Numeric(20, 6), nullable=True))',
                'op.add_column("ads_promotion_campaign_links", sa.Column("currency", sa.String(length=8), nullable=True))',
                'op.add_column("ads_promotion_campaign_links", sa.Column("objective_status", sa.String(length=16), nullable=True))',
                'op.add_column("ads_promotion_campaign_links", sa.Column("objective_updated_at", sa.DateTime(timezone=True), nullable=True))',
            )
            expected_drops = (
                'op.drop_column("ads_promotion_campaign_links", "objective_updated_at")',
                'op.drop_column("ads_promotion_campaign_links", "objective_status")',
                'op.drop_column("ads_promotion_campaign_links", "currency")',
                'op.drop_column("ads_promotion_campaign_links", "target_cpa")',
                'op.drop_column("ads_promotion_campaign_links", "objective_type")',
            )
            operations = re.findall(r"op\.([a-z_]+)\(", text)
            table_names = set(re.findall(r'"(ads_[a-z0-9_]+)"', text.lower()))
            explicit_downgrade = all(item in normalized for item in expected_drops)
            loop_downgrade = 'for name in ("objective_updated_at", "objective_status", "currency", "target_cpa", "objective_type")' in normalized and 'op.drop_column("ads_promotion_campaign_links", name)' in normalized
            return (
                'revision = "0079_ads_objective_target_cpa_v1"' in text
                and 'down_revision = "0078_ads_bot_ai_support_v1"' in text
                and set(operations) <= {"add_column", "drop_column"}
                and operations.count("add_column") == 5
                and (operations.count("drop_column") == 5 and explicit_downgrade or operations.count("drop_column") == 1 and loop_downgrade)
                and table_names == {"ads_promotion_campaign_links"}
                and all(item in normalized for item in expected_adds)
                and (explicit_downgrade or loop_downgrade)
                and not any(marker in text.lower() for marker in frozen_markers)
            )
        if path == "alembic/versions/0080_creative_storage_intelligence_v1.py":
            normalized = re.sub(r"\s+", " ", text).strip()
            allowed_adds = {
                'op.add_column("ads_creatives", sa.Column("source_type", sa.String(length=32), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("storage_provider", sa.String(length=32), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("storage_key", sa.String(length=1024), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("media_storage_state", sa.String(length=32), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("mime_type", sa.String(length=128), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("file_size", sa.BigInteger(), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("width", sa.Integer(), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("height", sa.Integer(), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("duration_seconds", sa.Numeric(20, 6), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("ai_features_json", sa.Text(), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))',
                'op.add_column("ads_creatives", sa.Column("provider_state", sa.String(length=64), nullable=True))',
            }
            operations = re.findall(r"op\.([a-z_]+)\(", text)
            table_names = set(re.findall(r'"(ads_[a-z0-9_]+)"', text.lower()))
            return (
                'revision = "0080_creative_storage_intelligence_v1"' in text
                and 'down_revision = "0079_ads_objective_target_cpa_v1"' in text
                and table_names == {"ads_creatives"}
                and all(item in normalized for item in allowed_adds)
                and operations.count("add_column") == 12
                and operations.count("create_index") == 4
                and operations.count("drop_index") == 1
                and operations.count("drop_column") == 1
                and 'for name in ("ix_ads_creatives_last_seen_at", "ix_ads_creatives_media_storage_state", "ix_ads_creatives_storage_key", "ix_ads_creatives_source_type")' in normalized
                and 'for name in ("provider_state", "last_seen_at", "ai_features_json", "duration_seconds", "height", "width", "file_size", "mime_type", "media_storage_state", "storage_key", "storage_provider", "source_type")' in normalized
                and 'op.execute("UPDATE ads_creatives SET ai_features_json = \'{}\' WHERE ai_features_json IS NULL")' in normalized
                and not any(marker in text.lower() for marker in frozen_markers)
            )
        table_names = set(re.findall(r'"(ads_[a-z0-9_]+)"', text.lower()))
        allowed_tables = {"ads_analysis_snapshots", "ads_ai_runs", "ads_recommendations", "ads_ai_outcomes", "ads_memories", "ads_memory_evidence", "ads_data_health", "ads_observations", "ads_collection_runs", "ads_report_runs"}
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
approval_paths = [p for p in protected if owner_task and any(a == p for a in approved)]
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
