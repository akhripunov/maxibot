#!/usr/bin/env bash
set -Eeuo pipefail

# Canonical MAXIBOT production release entrypoint. Default is non-mutating.

usage() {
  cat <<'EOF'
usage: scripts/release_prod.sh FULL_SHA [--dry-run|--apply]

--apply requires OWNER_APPROVED=1, PRODUCTION_HOST and PRODUCTION_SSH_KEY.
Production secrets are read only from /opt/maxibot/.env.
TARGET_MIGRATION must name the intended target revision explicitly; the script
never guesses a branch, even when the source currently has one head.
EOF
}
die() { echo "RELEASE_BLOCKED=$*" >&2; exit 1; }
say() { printf '%s=%s\n' "$1" "$2"; }
timed_stage() {
  local name=$1 started finished code
  shift
  started=$(date +%s)
  printf 'RELEASE_STAGE=%s_START\n' "$name" >&2
  if "$@"; then code=0; else code=$?; fi
  finished=$(date +%s)
  printf 'STAGE_SECONDS_%s=%s\n' "$name" "$((finished - started))" >&2
  return "$code"
}

# Only wrap idempotent reads in this retry loop. Callers must never pass a
# mutation command here; apply-stage writes continue to fail closed as-is.
NETWORK_RETRY_BUDGET_SECONDS=1800
NETWORK_RETRY_SPENT_SECONDS=0
if [[ -n "${NETWORK_RETRY_BUDGET_STATE:-}" && -f "$NETWORK_RETRY_BUDGET_STATE" ]]; then
  NETWORK_RETRY_SPENT_SECONDS=$(<"$NETWORK_RETRY_BUDGET_STATE")
  [[ "$NETWORK_RETRY_SPENT_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "invalid shared network retry budget state"
fi
NETWORK_LAST_ERROR=
NETWORK_LAST_STATUS=
persist_network_retry_budget() {
  [[ -n "${NETWORK_RETRY_BUDGET_STATE:-}" ]] || return 0
  local temporary="${NETWORK_RETRY_BUDGET_STATE}.$$"
  printf '%s\n' "${1:-$NETWORK_RETRY_SPENT_SECONDS}" > "$temporary"
  mv -f "$temporary" "$NETWORK_RETRY_BUDGET_STATE"
}
network_retry_read() {
  local stage=$1 attempt=0 cap=1 remaining delay elapsed output error status started=$SECONDS
  shift
  NETWORK_LAST_ERROR=
  NETWORK_LAST_STATUS=
  local stderr_file
  stderr_file=$(mktemp "${TMPDIR:-/tmp}/maxibot-network-retry.XXXXXX") || return 1
  while :; do
    : > "$stderr_file"
    if [[ -n "${NETWORK_RETRY_STDIN_FILE:-}" ]]; then
      if output=$("$@" < "$NETWORK_RETRY_STDIN_FILE" 2>"$stderr_file"); then status=0; else status=$?; fi
    else
      if output=$("$@" 2>"$stderr_file"); then status=0; else status=$?; fi
    fi
    error=$(cat "$stderr_file")
    if [[ "$status" == 0 ]]; then
      NETWORK_LAST_ATTEMPTS=$((attempt + 1))
      elapsed=$((SECONDS - started))
      if (( elapsed >= NETWORK_RETRY_BUDGET_SECONDS )); then
        NETWORK_LAST_STATUS=NETWORK_UNAVAILABLE
        printf 'NETWORK_UNAVAILABLE stage=%s elapsed_s=%s budget_s=%s\n' "$stage" "$elapsed" "$NETWORK_RETRY_BUDGET_SECONDS" >&2
        rm -f "$stderr_file"
        return 75
      fi
      NETWORK_LAST_STATUS=PASS
      if (( attempt > 0 )); then
        NETWORK_RETRY_SPENT_SECONDS=$((NETWORK_RETRY_SPENT_SECONDS + elapsed))
        persist_network_retry_budget
        printf 'NETWORK_RESTORED stage=%s elapsed_s=%s\n' "$stage" "$elapsed" >&2
      fi
      printf 'RELEASE_CONTINUING stage=%s\n' "$stage" >&2
      printf '%s' "$output"
      rm -f "$stderr_file"
      return 0
    fi
    NETWORK_LAST_ERROR=$error
    if [[ "$status" != 255 ]] || [[ "$error" =~ [Pp]ermission\ denied|[Hh]ost\ key\ verification\ failed|[Aa]uthentication\ failed|[Nn]o\ matching\ host\ key|[Tt]oo\ many\ authentication ]]; then
      NETWORK_LAST_STATUS=BLOCKED
      rm -f "$stderr_file"
      return "$status"
    fi
    if ! [[ "$error" =~ [Cc]ould\ not\ resolve|[Nn]odename\ nor\ servname|[Tt]emporary\ failure\ in\ name\ resolution|[Nn]ame\ or\ service\ not\ known|[Ee][Aa][Ii]_[Aa][Gg][Aa][Ii][Nn]|[Ee][Nn][Oo][Ff][Oo][Uu][Nn][Dd]|[Cc]onnection\ timed\ out|[Oo]peration\ timed\ out|[Cc]onnection\ reset|[Cc]onnection\ closed|[Bb]anner\ exchange|[Cc]onnection\ refused|[Nn]etwork\ is\ unreachable|[Nn]o\ route\ to\ host|[Ss]oftware\ caused\ connection\ abort|[Ee]nd\ of\ file|[Ss]ocket\ is\ not\ connected ]]; then
      NETWORK_LAST_STATUS=BLOCKED
      rm -f "$stderr_file"
      return "$status"
    fi
    elapsed=$((SECONDS - started))
    remaining=$((NETWORK_RETRY_BUDGET_SECONDS - NETWORK_RETRY_SPENT_SECONDS - elapsed))
    if (( remaining <= 0 )); then
      NETWORK_LAST_STATUS=NETWORK_UNAVAILABLE
      NETWORK_RETRY_SPENT_SECONDS=$NETWORK_RETRY_BUDGET_SECONDS
      persist_network_retry_budget
      printf 'NETWORK_UNAVAILABLE stage=%s elapsed_s=%s budget_s=%s\n' "$stage" "$elapsed" "$NETWORK_RETRY_BUDGET_SECONDS" >&2
      rm -f "$stderr_file"
      return 75
    fi
    delay=$((1 + RANDOM % cap))
    (( delay > remaining )) && delay=$remaining
    printf 'NETWORK_RETRYING stage=%s retry_in_s=%s elapsed_s=%s\n' "$stage" "$delay" "$elapsed" >&2
    attempt=$((attempt + 1))
    sleep "$delay"
    elapsed=$((SECONDS - started))
    persist_network_retry_budget "$((NETWORK_RETRY_SPENT_SECONDS + elapsed))"
    if (( cap < 32 )); then cap=$((cap * 2)); else cap=60; fi
  done
}

[[ $# -ge 1 ]] || { usage; exit 2; }
TARGET_SHA=$1
MODE=DRY_RUN
shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE=DRY_RUN ;;
    --apply) MODE=APPLY ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

[[ "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]] || die "full 40-hex SHA required"
git cat-file -e "$TARGET_SHA^{commit}" 2>/dev/null || die "unknown commit"
# Prospective policy activation: immutable candidates must contain this policy
# module in their own tree. Historical pre-policy SHAs remain governed by the
# release policy that existed when they were created.
git cat-file -e "$TARGET_SHA:scripts/release_policy.py" 2>/dev/null || die "candidate predates prospective release policy"
say QA_POLICY ADVISORY_ONLY
say AUTO_REVIEW_REQUIRES_QA NO
ROOT_DIR=$(git rev-parse --show-toplevel)
COMPOSE_TEMPLATE="$ROOT_DIR/docker-compose.vps.yml"
[[ -f "$COMPOSE_TEMPLATE" ]] || die "docker-compose.vps.yml missing"
grep -q '^  api:' "$COMPOSE_TEMPLATE" || die "api service missing"
grep -q '^  worker:' "$COMPOSE_TEMPLATE" || die "worker service missing"
grep -q 'MAX_RUN_POLLING: "false"' "$COMPOSE_TEMPLATE" || die "api polling guard missing"
grep -q 'MAX_RUN_POLLING: "true"' "$COMPOSE_TEMPLATE" || die "worker polling guard missing"

TARGET_MIGRATION_HINT=${TARGET_MIGRATION:-}
MIGRATION_META=$(TARGET_MIGRATION_HINT="$TARGET_MIGRATION_HINT" python3 - "$TARGET_SHA" <<'PY'
import ast
import os
import re
import subprocess
import sys

sha = sys.argv[1]
files = subprocess.check_output(
    ["git", "ls-tree", "-r", "--name-only", sha, "alembic/versions"], text=True
).splitlines()
files = [name for name in files if name.endswith(".py") and "/__pycache__/" not in name]
revisions = {}
for name in files:
    try:
        text = subprocess.check_output(["git", "show", f"{sha}:{name}"], text=True)
    except subprocess.CalledProcessError:
        continue
    revision = re.search(r"^revision\s*=\s*([^\n]+)", text, re.M)
    down = re.search(r"^down_revision\s*=\s*([^\n]+)", text, re.M)
    if not revision:
        continue
    try:
        rev = ast.literal_eval(revision.group(1).strip())
        parent = ast.literal_eval(down.group(1).strip()) if down else None
    except (ValueError, SyntaxError):
        continue
    revisions[rev] = (name, parent)
hint = os.environ.get("TARGET_MIGRATION_HINT")
def parents_of(value):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)):
        return tuple(parent for parent in value if isinstance(parent, str))
    return ()

parents = {parent for _, down_revision in revisions.values() for parent in parents_of(down_revision)}
heads = sorted(rev for rev in revisions if rev not in parents)
if hint:
    if hint not in revisions:
        raise SystemExit(f"requested Alembic migration is absent: {hint}")
    if hint not in heads:
        raise SystemExit(f"requested Alembic migration is not a reachable head: {hint}")
    head = hint
else:
    raise SystemExit(f"TARGET_MIGRATION must be set explicitly; available heads: {heads}")
name, parent = revisions[head]
print(f"{name}|{head}|{','.join(parents_of(parent))}")
PY
)
IFS='|' read -r TARGET_MIGRATION_FILE TARGET_MIGRATION TARGET_DOWN <<< "$MIGRATION_META"
[[ -n "$TARGET_MIGRATION" && -n "$TARGET_DOWN" ]] || die "migration metadata incomplete"

say RELEASE_ENTRYPOINT "scripts/release_prod.sh"
say TARGET_SHA "$TARGET_SHA"
say TARGET_MIGRATION "$TARGET_MIGRATION"
say TARGET_DOWN_REVISION "$TARGET_DOWN"
say CANONICAL_ENV_HANDLING "absolute:/opt/maxibot/.env"
say RELEASE_DIRECTORY_LAYOUT "/opt/maxibot/releases/<full-sha>/"
say IMAGE_PLATFORM_POLICY "linux/amd64"
say MIGRATION_BEFORE_SWITCH_READY_BLOCKED YES
HEALTH_TIMEOUT_SECONDS=${HEALTH_TIMEOUT_SECONDS:-60}
HEALTH_POLL_INTERVAL_SECONDS=${HEALTH_POLL_INTERVAL_SECONDS:-2}
[[ "$HEALTH_TIMEOUT_SECONDS" =~ ^[0-9]+$ && "$HEALTH_TIMEOUT_SECONDS" -gt 0 ]] || die "HEALTH_TIMEOUT_SECONDS must be a positive integer"
[[ "$HEALTH_POLL_INTERVAL_SECONDS" =~ ^[0-9]+$ && "$HEALTH_POLL_INTERVAL_SECONDS" -gt 0 ]] || die "HEALTH_POLL_INTERVAL_SECONDS must be a positive integer"
say HEALTH_READINESS_POLICY "bounded:${HEALTH_TIMEOUT_SECONDS}s/${HEALTH_POLL_INTERVAL_SECONDS}s"
WORKER_HEARTBEAT_MAX_AGE_SECONDS=${WORKER_HEARTBEAT_MAX_AGE_SECONDS:-90}
WORKER_BOOTSTRAP_FROM=
[[ "${WORKER_BOOTSTRAP_APPROVED:-0}" =~ ^[01]$ ]] || die "invalid worker bootstrap approval"
if [[ "${WORKER_BOOTSTRAP_APPROVED:-0}" == 1 ]]; then
  WORKER_BOOTSTRAP_FROM=206a1a144be4e0c5de52238c273f89e908d4a84d
  say WORKER_BOOTSTRAP_APPROVAL "explicit;legacy=$WORKER_BOOTSTRAP_FROM;primary_loop_unverified_until_postdeploy"
fi
WORKER_HEALTH_SAMPLE_SECONDS=${WORKER_HEALTH_SAMPLE_SECONDS:-35}
[[ "$WORKER_HEARTBEAT_MAX_AGE_SECONDS" =~ ^[0-9]+$ && "$WORKER_HEARTBEAT_MAX_AGE_SECONDS" -gt 0 ]] || die "WORKER_HEARTBEAT_MAX_AGE_SECONDS must be positive"
[[ "$WORKER_HEALTH_SAMPLE_SECONDS" =~ ^[0-9]+$ && "$WORKER_HEALTH_SAMPLE_SECONDS" -gt 0 ]] || die "WORKER_HEALTH_SAMPLE_SECONDS must be positive"
say WORKER_HEALTH_POLICY "heartbeat_max_age=${WORKER_HEARTBEAT_MAX_AGE_SECONDS}s;restart_delta_interval=${WORKER_HEALTH_SAMPLE_SECONDS}s;primary_heartbeat_required=true"

if [[ "$MODE" == DRY_RUN ]]; then
  say TARGET_SHA_PROVEN YES
  API_CANDIDATE_IMAGE=${API_CANDIDATE_IMAGE:-maxibot-vps-api:$TARGET_SHA}
  WORKER_CANDIDATE_IMAGE=${WORKER_CANDIDATE_IMAGE:-maxibot-vps-api:$TARGET_SHA}
  if ! IMAGE_PREFLIGHT=$(python3 "$ROOT_DIR/scripts/release_image_preflight.py" \
    "$TARGET_SHA" "$API_CANDIDATE_IMAGE" "$WORKER_CANDIDATE_IMAGE" 2>&1); then
    say IMAGE_READY NO
    say API_WORKER_IMAGE_PARITY BLOCKED
    say IMAGE_PREFLIGHT_REASON "$(printf '%s' "$IMAGE_PREFLIGHT" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
    say RELEASE_DIRECTORY_READY PLANNED_ONLY
    say ENV_READY PRODUCTION_PREFLIGHT_REQUIRED
    say COMPOSE_RESOLUTION_READY TEMPLATE_VALIDATED
    say RUNTIME_SWITCH_READY NOT_EXECUTED
    say ROLLBACK_READY METADATA_REQUIRED
    say MIGRATION_ALLOWED NO
    say SAFE_TO_RELEASE NO
    say DRY_RUN_RESULT PASS
    exit 0
  fi
  while IFS='=' read -r key value; do [[ -n "$key" ]] && say "$key" "$value"; done <<<"$IMAGE_PREFLIGHT"
  say API_WORKER_IMAGE_PARITY PASS
  say RELEASE_DIRECTORY_READY PLANNED_ONLY
  say COMPOSE_RESOLUTION_READY TEMPLATE_VALIDATED
  say RUNTIME_SWITCH_READY NOT_EXECUTED
  say MIGRATION_ALLOWED NO
  # A dry-run may attest a prebuilt image and perform read-only production
  # compatibility checks. It must never create a release directory, backup,
  # migration, container, or runtime switch.
  CANONICAL_PRODUCTION_HOST=135.106.166.46
  PRODUCTION_HOST=${PRODUCTION_HOST:-${MAXIBOT_PRODUCTION_HOST:-$CANONICAL_PRODUCTION_HOST}}
  PRODUCTION_SSH_KEY=${PRODUCTION_SSH_KEY:-${MAXIBOT_PRODUCTION_SSH_KEY:-/Users/akhripuniov/.ssh/maxibot_selectel_ed25519}}
  PREFLIGHT_OK=1
  if [[ "$PRODUCTION_HOST" != "$CANONICAL_PRODUCTION_HOST" || ! -r "$PRODUCTION_SSH_KEY" ]]; then
    PREFLIGHT_OK=0
    if [[ "$PRODUCTION_HOST" != "$CANONICAL_PRODUCTION_HOST" ]]; then
      say SSH_REMOTE_PREFLIGHT BLOCKED_NONCANONICAL_HOST
    else
      say SSH_REMOTE_PREFLIGHT BLOCKED_SSH_KEY_UNREADABLE
    fi
  else
    PREFLIGHT_TARGET="root@$PRODUCTION_HOST"
    PREFLIGHT_SSH=(ssh -i "$PRODUCTION_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 "$PREFLIGHT_TARGET")
    PROD_DB_HEAD=
    PROD_READY=
    PROD_API_IMAGE=
    PROD_WORKER_IMAGE=
    PROD_WORKER_HEALTH=ERROR
    ROLLBACK_CONFIG=
    PROD_WRITE_FLAGS=
    # Keep the read-only production checks in one authenticated SSH session.
    # Repeated handshakes made the old preflight intermittently lose the SSH
    # banner and hid which check had failed behind a generic BLOCKED result.
    PREFLIGHT_SCRIPT_FILE=$(mktemp "${TMPDIR:-/tmp}/maxibot-preflight.XXXXXX")
    cat > "$PREFLIGHT_SCRIPT_FILE" <<'REMOTE_PREFLIGHT'
set -u

check_value() {
  key=$1
  shift
  if value=$("$@" 2>/dev/null); then
    value=${value//$'\n'/,}
    printf '%s=%s\n' "$key" "$value"
  else
    printf '%s=ERROR\n' "$key"
  fi
}

check_value PROD_DB_HEAD docker exec maxibot-vps-db-1 psql -U maxibot -d maxibot -At -c 'select version_num from alembic_version'
check_value PROD_READY docker exec maxibot-vps-api-1 python -c 'import urllib.request,json; print(json.load(urllib.request.urlopen("http://127.0.0.1:8000/health/ready"))["status"])'
check_value PROD_API_IMAGE docker inspect maxibot-vps-api-1 --format '{{.Config.Image}}'
check_value PROD_WORKER_IMAGE docker inspect maxibot-vps-worker-1 --format '{{.Config.Image}}'
check_value ROLLBACK_CONFIG docker inspect maxibot-vps-api-1 --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}'

if env_output=$(docker inspect maxibot-vps-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null); then
  write_flags=$(printf '%s\n' "$env_output" | grep -E '^(ADS_AUTOPILOT_ENABLED|ADS_WRITE_ACTIONS_ENABLED)=' | paste -sd, - || true)
  printf 'PROD_WRITE_FLAGS=%s\n' "$write_flags"
else
  printf 'PROD_WRITE_FLAGS=ERROR\n'
fi
unset env_output
REMOTE_PREFLIGHT
    # Stream the shared stdlib health collector; it is never installed on VPS.
    printf "\nif worker_health=\$(python3 - --interval %s --max-age %s %s <<'WORKER_HEALTH_PY'\n" "$WORKER_HEALTH_SAMPLE_SECONDS" "$WORKER_HEARTBEAT_MAX_AGE_SECONDS" "${WORKER_BOOTSTRAP_FROM:+--bootstrap-from $WORKER_BOOTSTRAP_FROM}" >> "$PREFLIGHT_SCRIPT_FILE"
    cat "$ROOT_DIR/app/worker_health.py" "$ROOT_DIR/scripts/worker_health_preflight.py" >> "$PREFLIGHT_SCRIPT_FILE"
    printf '\nWORKER_HEALTH_PY\n); then\n  printf "PROD_WORKER_HEALTH=%%s\\n" "$worker_health"\nelse\n  printf "PROD_WORKER_HEALTH=ERROR\\n"\n  printf "WORKER_HEALTH_DETAIL=%%s\\n" "$worker_health"\nfi\n' >> "$PREFLIGHT_SCRIPT_FILE"
    NETWORK_RETRY_STDIN_FILE=$PREFLIGHT_SCRIPT_FILE
    if PREFLIGHT_OUTPUT=$(network_retry_read production_preflight "${PREFLIGHT_SSH[@]}" 'bash -s'); then
      say SSH_REMOTE_PREFLIGHT PASS
      PREFLIGHT_ERRORS=
      while IFS='=' read -r key value; do
        case "$key" in
          PROD_DB_HEAD) PROD_DB_HEAD=$value ;;
          PROD_READY) PROD_READY=$value ;;
          PROD_API_IMAGE) PROD_API_IMAGE=$value ;;
          PROD_WORKER_IMAGE) PROD_WORKER_IMAGE=$value ;;
          PROD_WORKER_HEALTH) PROD_WORKER_HEALTH=$value ;;
          WORKER_HEALTH_DETAIL) say WORKER_HEALTH_DETAIL "$value"; continue ;;
          ROLLBACK_CONFIG) ROLLBACK_CONFIG=$value ;;
          PROD_WRITE_FLAGS) PROD_WRITE_FLAGS=$value ;;
          *) continue ;;
        esac
        if [[ "$value" == ERROR ]]; then
          PREFLIGHT_ERRORS="${PREFLIGHT_ERRORS:+$PREFLIGHT_ERRORS,}$key"
          continue
        fi
        say "$key" "$value"
      done <<< "$PREFLIGHT_OUTPUT"
      if [[ -n "$PREFLIGHT_ERRORS" ]]; then
        say PRODUCTION_PREFLIGHT_FAILED_CHECKS "$PREFLIGHT_ERRORS"
        PREFLIGHT_OK=0
      fi
      if [[ -n "$WORKER_BOOTSTRAP_FROM" && ( "$PROD_DB_HEAD" != "$TARGET_MIGRATION" || "$PROD_API_IMAGE" != "maxibot-vps-api:$WORKER_BOOTSTRAP_FROM" || "$PROD_WORKER_IMAGE" != "$PROD_API_IMAGE" ) ]]; then
        PREFLIGHT_OK=0
        say WORKER_BOOTSTRAP_SCOPE FAIL
      fi
      DB_REHEARSAL_OK=0
      if [[ "$PROD_DB_HEAD" == "$TARGET_MIGRATION" ]]; then
        DB_REHEARSAL_OK=1
        say MIGRATION_REHEARSAL "PASS (already at target)"
      elif [[ "$PROD_DB_HEAD" != ERROR && -n "$PROD_DB_HEAD" ]] && MIGRATION_REHEARSAL=$(python3 "$ROOT_DIR/scripts/migration_rehearsal.py" "$TARGET_SHA" "$PROD_DB_HEAD" "$TARGET_MIGRATION" 2>&1); then
        DB_REHEARSAL_OK=1
        say MIGRATION_REHEARSAL "$MIGRATION_REHEARSAL"
      else
        say MIGRATION_REHEARSAL "BLOCKED (production DB head unavailable or rehearsal failed)"
      fi
      if [[ "$PROD_WORKER_HEALTH" == ERROR || "$DB_REHEARSAL_OK" != 1 || "$PROD_READY" != ready || -z "$PROD_API_IMAGE" || "$PROD_API_IMAGE" == ERROR || "$PROD_API_IMAGE" != "$PROD_WORKER_IMAGE" || "$PROD_WORKER_IMAGE" == ERROR || -z "$ROLLBACK_CONFIG" || "$ROLLBACK_CONFIG" == ERROR || "$PROD_WRITE_FLAGS" == ERROR || "$PROD_WRITE_FLAGS" != *"ADS_AUTOPILOT_ENABLED=false"* || "$PROD_WRITE_FLAGS" != *"ADS_WRITE_ACTIONS_ENABLED=false"* ]]; then
        PREFLIGHT_OK=0
      fi
    else
      PREFLIGHT_OK=0
      SSH_FAILURE_DETAIL="safe read-only SSH preflight failed; see retry status above"
      say SSH_REMOTE_PREFLIGHT FAIL
      [[ -z "$SSH_FAILURE_DETAIL" ]] || say SSH_FAILURE_DETAIL "$SSH_FAILURE_DETAIL"
    fi
    rm -f "$PREFLIGHT_SCRIPT_FILE"
    unset NETWORK_RETRY_STDIN_FILE
  fi
  if [[ "$PREFLIGHT_OK" == 1 ]]; then
    say ENV_READY PASS
    say DB_TARGET_COMPATIBILITY PASS
    say ROLLBACK_READY PASS
    say PRODUCTION_REHEARSAL PASS
    say SAFE_TO_RELEASE YES
    say RELEASE_READY YES
  else
    say ENV_READY PRODUCTION_PREFLIGHT_REQUIRED
    say DB_TARGET_COMPATIBILITY BLOCKED
    say ROLLBACK_READY METADATA_REQUIRED
    say PRODUCTION_REHEARSAL BLOCKED
    say SAFE_TO_RELEASE NO
    say RELEASE_READY NO
  fi
  say DRY_RUN_RESULT PASS
  exit 0
fi

[[ "${OWNER_APPROVED:-0}" == 1 ]] || die "--apply requires OWNER_APPROVED=1"
CANONICAL_PRODUCTION_HOST=135.106.166.46
PRODUCTION_HOST=${PRODUCTION_HOST:-${MAXIBOT_PRODUCTION_HOST:-$CANONICAL_PRODUCTION_HOST}}
PRODUCTION_SSH_KEY=${PRODUCTION_SSH_KEY:-${MAXIBOT_PRODUCTION_SSH_KEY:-/Users/akhripuniov/.ssh/maxibot_selectel_ed25519}}
PRODUCTION_ROOT=${PRODUCTION_ROOT:-${MAXIBOT_PRODUCTION_ROOT:-/opt/maxibot}}
[[ -n "$PRODUCTION_HOST" ]] || die "PRODUCTION_HOST is required"
[[ "$PRODUCTION_HOST" == "$CANONICAL_PRODUCTION_HOST" ]] || die "canonical production host required: $CANONICAL_PRODUCTION_HOST"
[[ -r "$PRODUCTION_SSH_KEY" ]] || die "PRODUCTION_SSH_KEY is not readable"
IMAGE_ARCHIVE=${RELEASE_IMAGE_ARCHIVE:-}
[[ -n "$IMAGE_ARCHIVE" && -r "$IMAGE_ARCHIVE" ]] || die "A verified Prepare image archive is required; source rebuilds are disabled"
RELEASE_IMAGE_ID=${RELEASE_IMAGE_ID:-}
[[ "$RELEASE_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || die "RELEASE_IMAGE_ID must be the verified image config digest"
RELEASE_IMAGE_ARCHIVE_SHA256=${RELEASE_IMAGE_ARCHIVE_SHA256:-}
[[ "$RELEASE_IMAGE_ARCHIVE_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "RELEASE_IMAGE_ARCHIVE_SHA256 must be the verified archive checksum"
RELEASE_IMAGE_TRANSPORT=${RELEASE_IMAGE_TRANSPORT:-archive}
[[ "$RELEASE_IMAGE_TRANSPORT" == archive || "$RELEASE_IMAGE_TRANSPORT" == ghcr-oci ]] || die "unsupported prepared image transport"
if [[ "$RELEASE_IMAGE_TRANSPORT" == ghcr-oci ]]; then
  [[ -r "${RELEASE_IMAGE_MANIFEST_PATH:-}" ]] || die "GHCR Prepare manifest is unavailable"
  [[ "${RELEASE_IMAGE_REPOSITORY:-}" == ghcr.io/akhripunov/maxibot-vps-api ]] || die "GHCR repository is not allowlisted"
  [[ "${RELEASE_IMAGE_DIGEST:-}" =~ ^sha256:[0-9a-f]{64}$ ]] || die "GHCR manifest digest is invalid"
  if [[ "$MODE" == APPLY ]]; then
    [[ -n "${GHCR_READ_TOKEN:-}" ]] || die "read-only GHCR credential is unavailable"
  fi
fi

PRODUCTION_SSH_USER=root
PRODUCTION_TARGET="$PRODUCTION_SSH_USER@$PRODUCTION_HOST"
SSH_CONTROL_DIR=$(mktemp -d /private/tmp/mb.XXXXXX)
chmod 700 "$SSH_CONTROL_DIR"
SSH_CONTROL_PATH="$SSH_CONTROL_DIR/%C"
SSH_OPTIONS=(-i "$PRODUCTION_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ControlMaster=auto -o ControlPath="$SSH_CONTROL_PATH" -o ControlPersist=120)
SSH=(ssh "${SSH_OPTIONS[@]}" "$PRODUCTION_TARGET")
SCP=(scp "${SSH_OPTIONS[@]}")
WORK=
cleanup_release() {
  [[ -z "$WORK" ]] || rm -rf -- "$WORK"
  ssh -i "$PRODUCTION_SSH_KEY" -o BatchMode=yes -o ControlPath="$SSH_CONTROL_PATH" -O exit "$PRODUCTION_TARGET" >/dev/null 2>&1 || true
  rm -rf -- "$SSH_CONTROL_DIR"
}
trap cleanup_release EXIT
remote() { "${SSH[@]}" "$@"; }
remote_with_stdin() {
  local command=$1 payload=$2
  printf '%s' "$payload" | ssh "${SSH_OPTIONS[@]}" "$PRODUCTION_TARGET" "$command"
}
remote_read() {
  local stage=$1
  shift
  network_retry_read "$stage" remote "$@"
}
say RELEASE_SSH_USER "$PRODUCTION_SSH_USER"
say CANONICAL_TARGET "$PRODUCTION_TARGET"
SSH_PREFLIGHT_OK=0
if network_retry_read ssh_preflight "${SSH[@]}" true >/dev/null; then
  SSH_PREFLIGHT_OK=1
  say SSH_PREFLIGHT_ATTEMPTS "$NETWORK_LAST_ATTEMPTS"
fi
if [[ "$SSH_PREFLIGHT_OK" != 1 ]]; then
  [[ "$NETWORK_LAST_STATUS" == NETWORK_UNAVAILABLE ]] && die "NETWORK_UNAVAILABLE stage=ssh_preflight"
  die "SSH preflight blocked by a permanent or unclassified transport error"
fi
say SSH_PREFLIGHT PASS
# Resolve the live production provenance before creating the remote release
# context.  A candidate is compatible only when its migration parent is the
# database head currently running in production.
CURRENT_PRODUCTION_SHA=$(remote_read production_sha "docker exec maxibot-vps-api-1 python -c 'import urllib.request,json; print(json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\"))[\"commit_sha\"])'")
CURRENT_PRODUCTION_DB_HEAD=$(remote_read production_db_head "docker exec maxibot-vps-db-1 psql -U maxibot -d maxibot -At -c 'select version_num from alembic_version'")
[[ "$CURRENT_PRODUCTION_SHA" =~ ^[0-9a-f]{40}$ ]] || die "current production SHA is unavailable"
[[ -n "$CURRENT_PRODUCTION_DB_HEAD" ]] || die "current production DB head is unavailable"
say CURRENT_PRODUCTION_SHA "$CURRENT_PRODUCTION_SHA"
say CURRENT_PRODUCTION_DB_HEAD "$CURRENT_PRODUCTION_DB_HEAD"
verify_task_authorization_before_mutation() {
  [[ "${RELEASE_MODE:-}" == "task-scoped-auto" ]] || return 0
  [[ -n "${TASK_AUTHORIZATION_ID:-}" && -n "${GITHUB_TOKEN:-}" && -n "${MAXIBOT_PYTHON:-}" ]] \
    || die "TASK_SCOPED_AUTO authorization context is unavailable before production mutation"
  CURRENT_PRODUCTION_SHA="$CURRENT_PRODUCTION_SHA" TARGET_SHA="$TARGET_SHA" "$MAXIBOT_PYTHON" - <<'PY'
import os
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path.cwd() / "scripts"))
    from scripts.run_release_workflow import verify_task_authorization
    verify_task_authorization(os.environ["TASK_AUTHORIZATION_ID"],
                             os.environ["TARGET_SHA"],
                             os.environ["CURRENT_PRODUCTION_SHA"],
                             require_claim=True)
except Exception:
    print("TASK_AUTHORIZATION_MUTATION_GATE=BLOCKED", file=sys.stderr)
    raise SystemExit(1)
print("TASK_AUTHORIZATION_MUTATION_GATE=PASS")
PY
  [[ "$?" == 0 ]] || die "TASK_SCOPED_AUTO authorization revalidation failed before production mutation"
}
if [[ -n "$WORKER_BOOTSTRAP_FROM" ]]; then
  [[ "$CURRENT_PRODUCTION_SHA" == "$WORKER_BOOTSTRAP_FROM" && "$CURRENT_PRODUCTION_DB_HEAD" == "$TARGET_MIGRATION" ]] || die "worker bootstrap forbids changed legacy SHA or pending migrations"
fi
MIGRATION_PATH=$(python3 - "$TARGET_SHA" "$CURRENT_PRODUCTION_DB_HEAD" "$TARGET_MIGRATION" <<'PY'
import ast
import re
import subprocess
import sys

sha, current, target = sys.argv[1:]
files = subprocess.check_output(
    ["git", "ls-tree", "-r", "--name-only", sha, "alembic/versions"], text=True
).splitlines()
files = [name for name in files if name.endswith(".py") and "/__pycache__/" not in name]
revisions = {}
for name in files:
    text = subprocess.check_output(["git", "show", f"{sha}:{name}"], text=True)
    revision = re.search(r"^revision\s*=\s*([^\n]+)", text, re.M)
    down = re.search(r"^down_revision\s*=\s*([^\n]+)", text, re.M)
    if not revision:
        continue
    try:
        rev = ast.literal_eval(revision.group(1).strip())
        parent = ast.literal_eval(down.group(1).strip()) if down else None
    except (ValueError, SyntaxError):
        continue
    if not isinstance(rev, str):
        continue
    parents = () if parent is None else ((parent,) if isinstance(parent, str) else tuple(parent))
    if not all(isinstance(item, str) for item in parents):
        raise SystemExit("migration graph contains an invalid parent reference")
    revisions[rev] = parents

if current not in revisions:
    raise SystemExit(f"current production migration is absent from candidate graph: {current}")
if target not in revisions:
    raise SystemExit(f"target migration is absent from candidate graph: {target}")

# Enumerate forward paths from the live revision to the target.  More than one
# path means a merge/divergence that must be reviewed instead of guessed.
children = {revision: [] for revision in revisions}
for revision, parents in revisions.items():
    for parent in parents:
        if parent not in revisions:
            raise SystemExit(f"migration graph references unknown parent: {parent}")
        children[parent].append(revision)

paths = []
def walk(node, path, seen):
    if node == target:
        paths.append(tuple(path))
        return
    for child in sorted(children[node]):
        if child in seen:
            raise SystemExit("migration graph contains a cycle")
        if len(paths) > 1:
            return
        walk(child, path + [child], seen | {child})

walk(current, [current], {current})
if not paths:
    raise SystemExit(
        f"no forward migration path from {current} to {target} in candidate graph"
    )
if len(paths) != 1:
    raise SystemExit(
        f"ambiguous migration path from {current} to {target}: {len(paths)} paths"
    )
print("->".join(paths[0]))
PY
)
say MIGRATION_PATH "$MIGRATION_PATH"
if [[ "$TARGET_MIGRATION" == "$CURRENT_PRODUCTION_DB_HEAD" ]]; then
  say MIGRATION_ALREADY_AT_TARGET YES
else
  say MIGRATION_ALREADY_AT_TARGET NO
fi
say PRODUCTION_BASELINE_COMPATIBILITY PASS
WORK=$(mktemp -d)
git archive --format=tar "$TARGET_SHA" | tar -x -C "$WORK"

# macOS may leave AppleDouble/resource-fork metadata in a release context.
# Remove it before any build-context mutation and fail closed if anything
# remains; Alembic must never be asked to import a metadata pseudo-file.
find "$WORK" \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -exec rm -rf {} +
if find "$WORK" \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -print -quit | grep -q .; then
  die "AppleDouble or macOS metadata remains in release context"
fi

# Compose has one secret source; no symlinks or secret copies are created.
python3 - "$WORK/docker-compose.vps.yml" "$TARGET_SHA" <<'PY'
from pathlib import Path
import re
import sys
p = Path(sys.argv[1])
sha = sys.argv[2]
s = p.read_text()
s = s.replace("env_file: .env", "env_file: /opt/maxibot/.env")
s = s.replace("      - .env\n", "      - /opt/maxibot/.env\n")
s = re.sub(r"maxibot-vps-api:[^\s\"]+", f"maxibot-vps-api:{sha}", s)
s = re.sub(r'MAXIBOT_RELEASE_ID: "[^"]+"', f'MAXIBOT_RELEASE_ID: "{sha}"', s)
s = re.sub(r'MAXIBOT_RELEASE_MARKER: "[^"]+"', f'MAXIBOT_RELEASE_MARKER: "{sha}"', s)
s = re.sub(r'MAXIBOT_COMMIT_SHA: "[^"]+"', f'MAXIBOT_COMMIT_SHA: "{sha}"', s)
p.write_text(s)
PY
cat > "$WORK/release-manifest.json" <<EOF
{"release_id":"$TARGET_SHA","target_sha":"$TARGET_SHA","target_migration":"$TARGET_MIGRATION","down_revision":"$TARGET_DOWN","env_file":"/opt/maxibot/.env","image":"maxibot-vps-api:$TARGET_SHA","platform":"linux/amd64","prepared_image_config_digest":"$RELEASE_IMAGE_ID","image_archive_sha256":"$RELEASE_IMAGE_ARCHIVE_SHA256","image_transport":"$RELEASE_IMAGE_TRANSPORT","image_repository":"${RELEASE_IMAGE_REPOSITORY:-}","image_manifest_digest":"${RELEASE_IMAGE_DIGEST:-}"}
EOF
ARCHIVE="$WORK/../maxibot-release-$TARGET_SHA.tar"
tar --exclude='._*' --exclude='.DS_Store' --exclude='__MACOSX' -cf "$ARCHIVE" -C "$WORK" .
IMAGE_REUSE=NO
if [[ "$RELEASE_IMAGE_TRANSPORT" == ghcr-oci ]]; then
  GHCR_PULL_SCRIPT=$(python3 - "$RELEASE_IMAGE_MANIFEST_PATH" "$TARGET_SHA" "${PREPARE_RUN_ID:-}" "${PREPARE_RUN_ATTEMPT:-}" <<'PY'
import json, sys
from pathlib import Path
from scripts.maxibot_ghcr import validate_oci_image, create_vps_pull_script
proof = json.loads(Path(sys.argv[1]).read_text())
validated = validate_oci_image(proof, sys.argv[2], sys.argv[3], sys.argv[4])
print(create_vps_pull_script(validated, sys.argv[2]))
PY
  ) || die "GHCR pull command proof could not be generated"
  GHCR_PULL_OUTPUT=$(remote_with_stdin "$GHCR_PULL_SCRIPT" "$GHCR_READ_TOKEN") || die "GHCR exact-digest pull failed before production mutation"
  grep -q '^MAXIBOT_GHCR_IMAGE_VERIFIED=YES$' <<< "$GHCR_PULL_OUTPUT" || die "GHCR exact-digest verification marker missing"
  say GHCR_LAYER_REUSE NOT_MEASURED
  say GHCR_BYTES_TRANSFERRED NOT_MEASURED
  say GHCR_PULL_BY_DIGEST PASS
  say IMAGE_TRANSPORT ghcr-oci
  say IMAGE_MANIFEST_DIGEST "$RELEASE_IMAGE_DIGEST"
else
  IMAGE_REUSE=$(remote "if test -r $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.sha256 && test -r $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.config && test \"\$(cat $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.sha256)\" = $RELEASE_IMAGE_ARCHIVE_SHA256 && test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Id}}' 2>/dev/null)\" = \"\$(cat $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.config)\" && test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Os}}/{{.Architecture}}')\" = linux/amd64 && test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}')\" = $TARGET_SHA && test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{index .Config.Labels \"org.opencontainers.image.source-sha\"}}')\" = $TARGET_SHA && test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{index .Config.Labels \"org.maxibot.release.sha\"}}')\" = $TARGET_SHA; then echo YES; else echo NO; fi")
  say IMAGE_REUSED "$IMAGE_REUSE"
  say IMAGE_TRANSPORT archive
fi

# Stage release files only after an OCI pull has succeeded and its exact digest
# has been checked. Registry failure must not create a candidate release tree.
REMOTE_TMP="/tmp/maxibot-release-$TARGET_SHA.tar"
timed_stage SOURCE_UPLOAD "${SCP[@]}" "$ARCHIVE" "$PRODUCTION_TARGET:$REMOTE_TMP"
remote "set -eu; test -r $PRODUCTION_ROOT/.env; mkdir -p $PRODUCTION_ROOT/releases/$TARGET_SHA; tar -xf $REMOTE_TMP -C $PRODUCTION_ROOT/releases/$TARGET_SHA; find $PRODUCTION_ROOT/releases/$TARGET_SHA \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -exec rm -rf {} +; if find $PRODUCTION_ROOT/releases/$TARGET_SHA \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -print -quit | grep -q .; then echo 'AppleDouble or macOS metadata remains in remote release context' >&2; exit 1; fi"
remote "rm -f $REMOTE_TMP"
if [[ "$RELEASE_IMAGE_TRANSPORT" == ghcr-oci ]]; then
  remote "printf '%s\\n' '$RELEASE_IMAGE_DIGEST' > $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.oci; docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Id}}' > $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.config"
  remote "docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -Fx '$RELEASE_IMAGE_REPOSITORY@$RELEASE_IMAGE_DIGEST'"
fi
if [[ "$RELEASE_IMAGE_TRANSPORT" == archive && "$IMAGE_REUSE" != YES ]]; then
REMOTE_IMAGE_TMP="/tmp/maxibot-image-$TARGET_SHA.tar.gz"
timed_stage IMAGE_UPLOAD "${SCP[@]}" "$IMAGE_ARCHIVE" "$PRODUCTION_TARGET:$REMOTE_IMAGE_TMP"

timed_stage IMAGE_LOAD remote "set -eu; test \"\$(sha256sum $REMOTE_IMAGE_TMP | cut -d ' ' -f1)\" = $RELEASE_IMAGE_ARCHIVE_SHA256; docker load --input $REMOTE_IMAGE_TMP; docker image inspect maxibot-vps-api:$TARGET_SHA >/dev/null; test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Os}}/{{.Architecture}}')\" = linux/amd64; test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}')\" = $TARGET_SHA; test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{index .Config.Labels \"org.opencontainers.image.source-sha\"}}')\" = $TARGET_SHA; test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{index .Config.Labels \"org.maxibot.release.sha\"}}')\" = $TARGET_SHA; rm -f $REMOTE_IMAGE_TMP"
remote "printf '%s\\n' $RELEASE_IMAGE_ARCHIVE_SHA256 > $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.sha256; docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Id}}' > $PRODUCTION_ROOT/releases/$TARGET_SHA/prepared-image.config"
fi
REMOTE_LOADED_IMAGE_ID=$(remote "docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Id}}'")
[[ "$REMOTE_LOADED_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] || die "loaded VPS image config digest is unavailable"
remote "python3 $PRODUCTION_ROOT/releases/$TARGET_SHA/scripts/verify_loaded_prepared_image.py maxibot-vps-api:$TARGET_SHA $RELEASE_IMAGE_ID $TARGET_SHA"
if [[ "$RELEASE_IMAGE_TRANSPORT" == ghcr-oci ]]; then
  remote "docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -Fx '$RELEASE_IMAGE_REPOSITORY@$RELEASE_IMAGE_DIGEST'"
fi
say LOADED_IMAGE_CONTENT_PROOF PASS
say PREPARED_IMAGE_CONFIG_DIGEST "$RELEASE_IMAGE_ID"
say VPS_LOADED_IMAGE_CONFIG_DIGEST "$REMOTE_LOADED_IMAGE_ID"
say IMAGE_ARCHIVE_SHA256 "$RELEASE_IMAGE_ARCHIVE_SHA256"
PREVIOUS_CONFIG=$(remote "docker inspect maxibot-vps-api-1 --format '{{index .Config.Labels \"com.docker.compose.project.config_files\"}}'")
PREVIOUS_SHA=$(remote "docker inspect maxibot-vps-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^MAXIBOT_COMMIT_SHA=//p' | head -1")
PREVIOUS_IMAGE=$(remote "docker inspect maxibot-vps-api-1 --format '{{.Config.Image}}'")
CURRENT_ALEMBIC=$(remote "docker exec maxibot-vps-db-1 psql -U maxibot -d maxibot -At -c 'select version_num from alembic_version'")
[[ "$PREVIOUS_SHA" =~ ^[0-9a-f]{40}$ && -n "$PREVIOUS_IMAGE" && -n "$CURRENT_ALEMBIC" ]] || die "rollback metadata incomplete"
[[ "$PREVIOUS_IMAGE" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]*$ ]] || die "rollback image reference is invalid"
PREVIOUS_IMAGE_ID=$(remote "docker inspect maxibot-vps-api-1 --format '{{.Image}}'")
PREVIOUS_TAG_IMAGE_ID=$(remote "docker image inspect '$PREVIOUS_IMAGE' --format '{{.Id}}'")
PREVIOUS_IMAGE_PLATFORM=$(remote "docker image inspect '$PREVIOUS_IMAGE' --format '{{.Os}}/{{.Architecture}}'")
PREVIOUS_IMAGE_REPO_DIGESTS_RAW=$(remote "docker image inspect '$PREVIOUS_IMAGE' --format '{{json .RepoDigests}}'")
PREVIOUS_IMAGE_PROOF=$(python3 - "$PREVIOUS_IMAGE_ID" "$PREVIOUS_TAG_IMAGE_ID" "$PREVIOUS_IMAGE_PLATFORM" "$PREVIOUS_IMAGE_REPO_DIGESTS_RAW" <<'PY'
import json
import sys
from scripts.maxibot_ghcr import validate_rollback_image_identity

try:
    proof = validate_rollback_image_identity(
        sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4]))
except (ValueError, json.JSONDecodeError) as error:
    print(f"ROLLBACK_IMAGE_PROOF=BLOCKED reason={error}", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps(proof, sort_keys=True, separators=(",", ":")))
PY
) || die "previous production image identity is not provable"
PREVIOUS_IMAGE_IDENTITY=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["rollback_identity"])' "$PREVIOUS_IMAGE_PROOF")
PREVIOUS_IMAGE_REPO_DIGESTS=$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["repo_digests"],separators=(",",":")))' "$PREVIOUS_IMAGE_PROOF")
[[ "$PREVIOUS_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ && "$PREVIOUS_TAG_IMAGE_ID" == "$PREVIOUS_IMAGE_ID" ]] || die "running rollback image is not retained by its exact local image reference"
remote "test \"\$(docker image inspect '$PREVIOUS_IMAGE_ID' --format '{{.Id}}')\" = '$PREVIOUS_IMAGE_ID' && test \"\$(docker image inspect '$PREVIOUS_IMAGE' --format '{{.Id}}')\" = '$PREVIOUS_IMAGE_ID'" || die "rollback image is not locally available by its exact identity"
remote "set -eu; cp \"$PREVIOUS_CONFIG\" \"$PRODUCTION_ROOT/releases/$TARGET_SHA/rollback-compose.vps.yml\""
say PRE_DEPLOY_SHA "$PREVIOUS_SHA"
say PRE_DEPLOY_ALEMBIC_HEAD "$CURRENT_ALEMBIC"
say PREVIOUS_IMAGE_ID "$PREVIOUS_IMAGE_ID"
say PREVIOUS_IMAGE_PLATFORM "$PREVIOUS_IMAGE_PLATFORM"
say PREVIOUS_IMAGE_REPO_DIGESTS "$PREVIOUS_IMAGE_REPO_DIGESTS"
say ROLLBACK_IMAGE_IDENTITY "$PREVIOUS_IMAGE_IDENTITY"
say ROLLBACK_IMAGE_AVAILABILITY PASS

# A migration is not allowed to start until a validated, restricted, bounded
# database backup exists. The custom pg_dump format is compressed by postgres
# itself and can be inspected by pg_restore without exposing secrets.
BACKUP_PATH=$(remote "set -eu
backup_dir='$PRODUCTION_ROOT/backups'
free_kb=\$(df -Pk '$PRODUCTION_ROOT' | awk 'NR==2 {print \$4}')
test \"\$free_kb\" -ge 1048576
mkdir -p \"\$backup_dir\"
chmod 700 \"\$backup_dir\"
stamp=\$(date -u +%Y%m%dT%H%M%SZ)
backup_path=\"\$backup_dir/maxibot-db-${TARGET_SHA}-\${stamp}.dump\"
docker exec maxibot-vps-db-1 pg_dump -U maxibot -d maxibot --format=custom --no-owner --no-privileges > \"\$backup_path\"
test -s \"\$backup_path\"
chmod 600 \"\$backup_path\"
docker exec -i maxibot-vps-db-1 pg_restore --list < \"\$backup_path\" > /dev/null
find \"\$backup_dir\" -maxdepth 1 -type f -name 'maxibot-db-*.dump' -printf '%T@ %p\\n' | sort -nr | awk 'NR > 5 {sub(/^[^ ]+ /, \"\"); print}' | xargs -r rm -f --
printf '%s\\n' \$backup_path")
[[ "$BACKUP_PATH" == "$PRODUCTION_ROOT/backups/maxibot-db-${TARGET_SHA}-"*.dump ]] || die "backup path validation failed"
say BACKUP PASS
say BACKUP_VALIDATION PASS
say BACKUP_DISK_PREFLIGHT PASS
say BACKUP_PATH "$BACKUP_PATH"
say ROLLBACK_READY YES

# This is the migration safety gate. No DB write or runtime switch precedes it.
remote "set -eu; cd $PRODUCTION_ROOT/releases/$TARGET_SHA; docker compose -p maxibot-vps -f docker-compose.vps.yml config --quiet; docker compose -p maxibot-vps -f docker-compose.vps.yml config --services | grep -qx api; docker compose -p maxibot-vps -f docker-compose.vps.yml config --services | grep -qx worker"
remote "set -eu; test \"\$(docker info --format '{{.Architecture}}')\" = x86_64; test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Os}}/{{.Architecture}}')\" = linux/amd64"
# Resolve the effective compose configuration and prove both runtime roles
# point at the exact approved candidate before any migration can start.
EFFECTIVE_IMAGES=$(remote "set -eu; cd $PRODUCTION_ROOT/releases/$TARGET_SHA; docker compose -p maxibot-vps -f docker-compose.vps.yml config --format json | python3 -c 'import json,sys; s=json.load(sys.stdin)[\"services\"]; print(s[\"api\"].get(\"image\",\"\")); print(s[\"worker\"].get(\"image\",\"\"))'")
API_EFFECTIVE_IMAGE=$(printf '%s\n' "$EFFECTIVE_IMAGES" | sed -n '1p')
WORKER_EFFECTIVE_IMAGE=$(printf '%s\n' "$EFFECTIVE_IMAGES" | sed -n '2p')
[[ "$API_EFFECTIVE_IMAGE" == "maxibot-vps-api:$TARGET_SHA" ]] || die "effective API image does not match approved candidate"
[[ "$WORKER_EFFECTIVE_IMAGE" == "maxibot-vps-api:$TARGET_SHA" ]] || die "effective worker image does not match approved candidate"
say PRE_APPLY_API_IMAGE "$API_EFFECTIVE_IMAGE"
say PRE_APPLY_WORKER_IMAGE "$WORKER_EFFECTIVE_IMAGE"
say PRE_APPLY_IMAGE_PARITY PASS
remote "set -eu; docker run --rm --network maxibot-vps-internal --env-file $PRODUCTION_ROOT/.env -e MAX_RUN_POLLING=false maxibot-vps-api:$TARGET_SHA alembic heads | grep -q '^$TARGET_MIGRATION'"
say MIGRATION_ALLOWED YES

verify_task_authorization_before_mutation
timed_stage MIGRATION remote "set -eu; docker run --rm --network maxibot-vps-internal --env-file $PRODUCTION_ROOT/.env -e MAX_RUN_POLLING=false maxibot-vps-api:$TARGET_SHA alembic upgrade $TARGET_MIGRATION"
verify_task_authorization_before_mutation
timed_stage RUNTIME_SWITCH remote "set -eu; docker compose -p maxibot-vps -f $PRODUCTION_ROOT/releases/$TARGET_SHA/docker-compose.vps.yml up -d --no-build --force-recreate api worker"
if ! timed_stage HEALTH remote "set -Eeuo pipefail
deadline=\$((\$(date +%s) + $HEALTH_TIMEOUT_SECONDS))
while true; do
  state=\$(docker inspect --format '{{.State.Status}}' maxibot-vps-api-1 2>/dev/null || true)
  if [[ \"\$state\" != running ]]; then
    echo RUNTIME_HEALTH=FAIL_CONTAINER_EXITED >&2
    docker inspect maxibot-vps-api-1 >&2 || true
    docker logs --tail 200 maxibot-vps-api-1 >&2 || true
    exit 42
  fi
  if docker exec maxibot-vps-api-1 python -c 'import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\"); urllib.request.urlopen(\"http://127.0.0.1:8000/health/live\")'; then
    echo RUNTIME_HEALTH=PASS >&2
    exit 0
  fi
  if [[ \$(date +%s) -ge \$deadline ]]; then
    echo RUNTIME_HEALTH=FAIL_TIMEOUT >&2
    docker inspect maxibot-vps-api-1 >&2 || true
    docker logs --tail 200 maxibot-vps-api-1 >&2 || true
    exit 43
  fi
  sleep $HEALTH_POLL_INTERVAL_SECONDS
done"; then
  echo "POSTDEPLOY_HEALTH=FAIL" >&2
  [[ -n "$PREVIOUS_CONFIG" ]] && remote "docker compose -p maxibot-vps -f $PREVIOUS_CONFIG up -d --no-build --force-recreate api worker" || true
  die "runtime rollback attempted; DB rollback remains a separate safety decision"
fi
# A fresh baseline follows the intentional runtime switch. No restart waiver.
WORKER_HEALTH_SCRIPT=$(mktemp "${TMPDIR:-/tmp}/maxibot-worker-health.XXXXXX")
cat "$ROOT_DIR/app/worker_health.py" "$ROOT_DIR/scripts/worker_health_preflight.py" > "$WORKER_HEALTH_SCRIPT"
if WORKER_HEALTH=$("${SSH[@]}" "python3 - --expected-release $TARGET_SHA --interval $WORKER_HEALTH_SAMPLE_SECONDS --max-age $WORKER_HEARTBEAT_MAX_AGE_SECONDS" < "$WORKER_HEALTH_SCRIPT"); then
  say POSTDEPLOY_WORKER_HEALTH "$WORKER_HEALTH"
else
  rm -f "$WORKER_HEALTH_SCRIPT"
  say POSTDEPLOY_WORKER_HEALTH "$WORKER_HEALTH"
  die "dedicated worker functional health failed; runtime reconciliation required"
fi
rm -f "$WORKER_HEALTH_SCRIPT"
FINAL_SHA=$(remote "docker exec maxibot-vps-api-1 python -c 'import urllib.request,json; print(json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\"))[\"commit_sha\"])'")
FINAL_ALEMBIC=$(remote "docker exec maxibot-vps-db-1 psql -U maxibot -d maxibot -At -c 'select version_num from alembic_version'")
API_IMAGE=$(remote "docker inspect maxibot-vps-api-1 --format '{{.Config.Image}}'")
WORKER_IMAGE=$(remote "docker inspect maxibot-vps-worker-1 --format '{{.Config.Image}}'")
API_POLLING=$(remote "docker exec maxibot-vps-api-1 printenv MAX_RUN_POLLING")
WORKER_POLLING=$(remote "docker exec maxibot-vps-worker-1 printenv MAX_RUN_POLLING")
WORKER_WRITE_FLAGS=$(remote "docker inspect maxibot-vps-worker-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(ADS_AUTOPILOT_ENABLED|ADS_WRITE_ACTIONS_ENABLED)=' | paste -sd, - || true")
[[ "$FINAL_SHA" == "$TARGET_SHA" && "$FINAL_ALEMBIC" == "$TARGET_MIGRATION" ]] || die "postdeploy parity failed"
[[ "$API_IMAGE" == "$WORKER_IMAGE" && "$API_IMAGE" == "maxibot-vps-api:$TARGET_SHA" ]] || die "API/worker image parity failed"
if [[ "$RELEASE_IMAGE_TRANSPORT" == ghcr-oci ]]; then
  remote "docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -Fx '$RELEASE_IMAGE_REPOSITORY@$RELEASE_IMAGE_DIGEST'" || die "postdeploy OCI digest parity failed"
fi
remote "test \"\$(docker image inspect '$PREVIOUS_IMAGE_ID' --format '{{.Id}}')\" = '$PREVIOUS_IMAGE_ID' && test \"\$(docker image inspect '$PREVIOUS_IMAGE' --format '{{.Id}}')\" = '$PREVIOUS_IMAGE_ID'" || die "rollback image identity changed or disappeared during apply"
[[ "$API_POLLING" == false && "$WORKER_POLLING" == true ]] || die "polling role check failed"
[[ "$WORKER_WRITE_FLAGS" == *"ADS_AUTOPILOT_ENABLED=false"* && "$WORKER_WRITE_FLAGS" == *"ADS_WRITE_ACTIONS_ENABLED=false"* ]] || die "worker ADS write flags are not disabled"
remote "cat > $PRODUCTION_ROOT/releases/$TARGET_SHA/release-report.env <<EOF
RELEASE_ID=$TARGET_SHA
TARGET_SHA=$TARGET_SHA
PREVIOUS_SHA=$PREVIOUS_SHA
TARGET_IMAGE=maxibot-vps-api:$TARGET_SHA
PREPARED_IMAGE_CONFIG_DIGEST=$RELEASE_IMAGE_ID
VPS_LOADED_IMAGE_CONFIG_DIGEST=$REMOTE_LOADED_IMAGE_ID
IMAGE_ARCHIVE_SHA256=$RELEASE_IMAGE_ARCHIVE_SHA256
PREVIOUS_IMAGE=$PREVIOUS_IMAGE
PREVIOUS_IMAGE_ID=$PREVIOUS_IMAGE_ID
PREVIOUS_IMAGE_PLATFORM=$PREVIOUS_IMAGE_PLATFORM
PREVIOUS_IMAGE_REPO_DIGESTS=$PREVIOUS_IMAGE_REPO_DIGESTS
ROLLBACK_IMAGE_IDENTITY=$PREVIOUS_IMAGE_IDENTITY
ROLLBACK_IMAGE_AVAILABILITY=PASS
MIGRATION_FROM=$CURRENT_ALEMBIC
MIGRATION_TO=$TARGET_MIGRATION
MIGRATION_RESULT=PASS
RUNTIME_SWITCH_RESULT=PASS
HEALTH_RESULT=PASS
ROLLBACK_RESULT=NOT_RUN
FINAL_RUNTIME_SHA=$FINAL_SHA
FINAL_ALEMBIC_HEAD=$FINAL_ALEMBIC
FINAL_STATUS=PASS
EOF"
RELEASE_REF="refs/maxibot/release/$TARGET_SHA"
[[ "$(git rev-parse HEAD)" == "$TARGET_SHA" ]] || die "candidate checkout SHA changed before finalization"
[[ -z "$(git status --porcelain)" ]] || die "candidate checkout changed before finalization"
EXISTING_RELEASE_REF_SHA=$(git ls-remote --refs origin "$RELEASE_REF" | awk 'NR == 1 {print $1}')
[[ -z "$EXISTING_RELEASE_REF_SHA" || "$EXISTING_RELEASE_REF_SHA" == "$TARGET_SHA" ]] || die "immutable release ref already names another candidate"
timed_stage FINALIZATION git push --force-with-lease="$RELEASE_REF:$EXISTING_RELEASE_REF_SHA" origin "$TARGET_SHA:$RELEASE_REF"
git update-ref "$RELEASE_REF" "$TARGET_SHA"
REMOTE_RELEASE_REF_SHA=$(git ls-remote --refs origin "$RELEASE_REF" | awk 'NR == 1 {print $1}')
[[ "$REMOTE_RELEASE_REF_SHA" == "$TARGET_SHA" ]] || die "remote release ref verification failed"
say RELEASE_REF "$RELEASE_REF"
say RELEASE_REF_PUSHED YES
say REMOTE_RELEASE_REF_SHA "$REMOTE_RELEASE_REF_SHA"
say FINAL_RUNTIME_SHA "$FINAL_SHA"
say FINAL_ALEMBIC_HEAD "$FINAL_ALEMBIC"
say API_WORKER_IMAGE_PARITY PASS
say POLLING_ROLES PASS
say ROLLBACK_IMAGE_ID "$PREVIOUS_IMAGE_ID"
say ROLLBACK_IMAGE_IDENTITY "$PREVIOUS_IMAGE_IDENTITY"
say ROLLBACK_IMAGE_AVAILABILITY PASS
say RELEASE_STATUS PASS
