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

if [[ "$MODE" == DRY_RUN ]]; then
  say TARGET_SHA_PROVEN YES
  say IMAGE_READY NOT_BUILT
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

[[ "${OWNER_APPROVED:-0}" == 1 ]] || die "--apply requires OWNER_APPROVED=1"
CANONICAL_PRODUCTION_HOST=135.106.166.46
PRODUCTION_HOST=${PRODUCTION_HOST:-${MAXIBOT_PRODUCTION_HOST:-$CANONICAL_PRODUCTION_HOST}}
PRODUCTION_SSH_KEY=${PRODUCTION_SSH_KEY:-${MAXIBOT_PRODUCTION_SSH_KEY:-/Users/akhripuniov/.ssh/maxibot_selectel_ed25519}}
PRODUCTION_ROOT=${PRODUCTION_ROOT:-${MAXIBOT_PRODUCTION_ROOT:-/opt/maxibot}}
[[ -n "$PRODUCTION_HOST" ]] || die "PRODUCTION_HOST is required"
[[ "$PRODUCTION_HOST" == "$CANONICAL_PRODUCTION_HOST" ]] || die "canonical production host required: $CANONICAL_PRODUCTION_HOST"
[[ -r "$PRODUCTION_SSH_KEY" ]] || die "PRODUCTION_SSH_KEY is not readable"

PRODUCTION_SSH_USER=root
PRODUCTION_TARGET="$PRODUCTION_SSH_USER@$PRODUCTION_HOST"
SSH=(ssh -i "$PRODUCTION_SSH_KEY" -o BatchMode=yes "$PRODUCTION_TARGET")
SCP=(scp -i "$PRODUCTION_SSH_KEY" -o BatchMode=yes)
remote() { "${SSH[@]}" "$@"; }
say RELEASE_SSH_USER "$PRODUCTION_SSH_USER"
say CANONICAL_TARGET "$PRODUCTION_TARGET"
if ! "${SSH[@]}" true; then
  die "SSH preflight failed for $PRODUCTION_TARGET"
fi
say SSH_PREFLIGHT PASS
# Resolve the live production provenance before creating the remote release
# context.  A candidate is compatible only when its migration parent is the
# database head currently running in production.
CURRENT_PRODUCTION_SHA=$(remote "docker exec maxibot-vps-api-1 python -c 'import urllib.request,json; print(json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\"))[\"commit_sha\"])'")
CURRENT_PRODUCTION_DB_HEAD=$(remote "docker exec maxibot-vps-db-1 psql -U maxibot -d maxibot -At -c 'select version_num from alembic_version'")
[[ "$CURRENT_PRODUCTION_SHA" =~ ^[0-9a-f]{40}$ ]] || die "current production SHA is unavailable"
[[ -n "$CURRENT_PRODUCTION_DB_HEAD" ]] || die "current production DB head is unavailable"
say CURRENT_PRODUCTION_SHA "$CURRENT_PRODUCTION_SHA"
say CURRENT_PRODUCTION_DB_HEAD "$CURRENT_PRODUCTION_DB_HEAD"
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
trap 'rm -rf "$WORK"' EXIT
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
{"release_id":"$TARGET_SHA","target_sha":"$TARGET_SHA","target_migration":"$TARGET_MIGRATION","down_revision":"$TARGET_DOWN","env_file":"/opt/maxibot/.env","image":"maxibot-vps-api:$TARGET_SHA","platform":"linux/amd64"}
EOF
ARCHIVE="$WORK/../maxibot-release-$TARGET_SHA.tar"
tar --exclude='._*' --exclude='.DS_Store' --exclude='__MACOSX' -cf "$ARCHIVE" -C "$WORK" .
REMOTE_TMP="/tmp/maxibot-release-$TARGET_SHA.tar"
"${SCP[@]}" "$ARCHIVE" "$PRODUCTION_TARGET:$REMOTE_TMP"

remote "set -eu; test -r $PRODUCTION_ROOT/.env; mkdir -p $PRODUCTION_ROOT/releases/$TARGET_SHA; tar -xf $REMOTE_TMP -C $PRODUCTION_ROOT/releases/$TARGET_SHA; find $PRODUCTION_ROOT/releases/$TARGET_SHA \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -exec rm -rf {} +; if find $PRODUCTION_ROOT/releases/$TARGET_SHA \( -name '._*' -o -name '.DS_Store' -o -name '__MACOSX' \) -print -quit | grep -q .; then echo 'AppleDouble or macOS metadata remains in remote release context' >&2; exit 1; fi"
remote "rm -f $REMOTE_TMP"
remote "set -eu; cd $PRODUCTION_ROOT; docker build --platform linux/amd64 --label org.opencontainers.image.revision=$TARGET_SHA --label org.maxibot.release.sha=$TARGET_SHA -t maxibot-vps-api:$TARGET_SHA releases/$TARGET_SHA; test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{index .Config.Labels \"org.maxibot.release.sha\"}}')\" = $TARGET_SHA; test \"\$(docker image inspect maxibot-vps-api:$TARGET_SHA --format '{{.Os}}/{{.Architecture}}')\" = linux/amd64"
PREVIOUS_CONFIG=$(remote "docker inspect maxibot-vps-api-1 --format '{{index .Config.Labels \"com.docker.compose.project.config_files\"}}'")
PREVIOUS_SHA=$(remote "docker inspect maxibot-vps-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^MAXIBOT_COMMIT_SHA=//p' | head -1")
PREVIOUS_IMAGE=$(remote "docker inspect maxibot-vps-api-1 --format '{{.Config.Image}}'")
CURRENT_ALEMBIC=$(remote "docker exec maxibot-vps-db-1 psql -U maxibot -d maxibot -At -c 'select version_num from alembic_version'")
[[ "$PREVIOUS_SHA" =~ ^[0-9a-f]{40}$ && -n "$PREVIOUS_IMAGE" && -n "$CURRENT_ALEMBIC" ]] || die "rollback metadata incomplete"
remote "set -eu; cp \"$PREVIOUS_CONFIG\" \"$PRODUCTION_ROOT/releases/$TARGET_SHA/rollback-compose.vps.yml\""
say PRE_DEPLOY_SHA "$PREVIOUS_SHA"
say PRE_DEPLOY_ALEMBIC_HEAD "$CURRENT_ALEMBIC"

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
remote "set -eu; docker run --rm --network maxibot-vps-internal --env-file $PRODUCTION_ROOT/.env -e MAX_RUN_POLLING=false maxibot-vps-api:$TARGET_SHA alembic heads | grep -q '^$TARGET_MIGRATION'"
say MIGRATION_ALLOWED YES

remote "set -eu; docker run --rm --network maxibot-vps-internal --env-file $PRODUCTION_ROOT/.env -e MAX_RUN_POLLING=false maxibot-vps-api:$TARGET_SHA alembic upgrade $TARGET_MIGRATION"
remote "set -eu; docker compose -p maxibot-vps -f $PRODUCTION_ROOT/releases/$TARGET_SHA/docker-compose.vps.yml up -d --no-build --force-recreate api worker"
if ! remote "set -Eeuo pipefail
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
FINAL_SHA=$(remote "docker exec maxibot-vps-api-1 python -c 'import urllib.request,json; print(json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/health/ready\"))[\"commit_sha\"])'")
FINAL_ALEMBIC=$(remote "docker exec maxibot-vps-db-1 psql -U maxibot -d maxibot -At -c 'select version_num from alembic_version'")
API_IMAGE=$(remote "docker inspect maxibot-vps-api-1 --format '{{.Config.Image}}'")
WORKER_IMAGE=$(remote "docker inspect maxibot-vps-worker-1 --format '{{.Config.Image}}'")
API_POLLING=$(remote "docker exec maxibot-vps-api-1 printenv MAX_RUN_POLLING")
WORKER_POLLING=$(remote "docker exec maxibot-vps-worker-1 printenv MAX_RUN_POLLING")
[[ "$FINAL_SHA" == "$TARGET_SHA" && "$FINAL_ALEMBIC" == "$TARGET_MIGRATION" ]] || die "postdeploy parity failed"
[[ "$API_IMAGE" == "$WORKER_IMAGE" && "$API_IMAGE" == "maxibot-vps-api:$TARGET_SHA" ]] || die "API/worker image parity failed"
[[ "$API_POLLING" == false && "$WORKER_POLLING" == true ]] || die "polling role check failed"
remote "cat > $PRODUCTION_ROOT/releases/$TARGET_SHA/release-report.env <<EOF
RELEASE_ID=$TARGET_SHA
TARGET_SHA=$TARGET_SHA
PREVIOUS_SHA=$PREVIOUS_SHA
TARGET_IMAGE=maxibot-vps-api:$TARGET_SHA
PREVIOUS_IMAGE=$PREVIOUS_IMAGE
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
say FINAL_RUNTIME_SHA "$FINAL_SHA"
say FINAL_ALEMBIC_HEAD "$FINAL_ALEMBIC"
say API_WORKER_IMAGE_PARITY PASS
say POLLING_ROLES PASS
say RELEASE_STATUS PASS
