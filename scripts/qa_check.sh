#!/usr/bin/env bash
set -u -o pipefail

# MAXIBOT release gate.  A missing prerequisite is a failure, never a pass.
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

RELEASE_ID="$(date -u +%Y-%m-%dT%H%M%SZ)"
RELEASE_DATE="$(date -u +%Y-%m-%d)"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  CANDIDATE_SHA="$(git rev-parse HEAD)"
  PREVIOUS_GOOD_SHA="$(git log -2 --format=%H 2>/dev/null | sed -n '2p')"
else
  CANDIDATE_SHA="untracked-$(date -u +%Y%m%d%H%M%S)"
  PREVIOUS_GOOD_SHA="UNAVAILABLE"
fi
PREVIOUS_GOOD_SHA="${PREVIOUS_GOOD_SHA:-UNAVAILABLE}"
IMAGE="${QA_IMAGE:-maxibot:${CANDIDATE_SHA}}"
QA_COMPOSE_PROJECT="${QA_COMPOSE_PROJECT:-maxibot-qa-$(printf '%s' "$RELEASE_ID" | tr -cd '[:lower:][:digit:]_-')}"
QA_COMPOSE_PROJECT="${QA_COMPOSE_PROJECT// /}"
QA_COMPOSE_FILE="${QA_COMPOSE_FILE:-docker-compose.qa.yml}"
QA_STAGING_PORT="${QA_STAGING_PORT:-18000}"
STATUS_FILE="$ROOT_DIR/RELEASE_STATUS.md"
HISTORY_DIR="$ROOT_DIR/releases"
LOG_DIR="${QA_LOG_DIR:-$ROOT_DIR/.qa-logs/$RELEASE_ID}"
mkdir -p "$HISTORY_DIR" "$LOG_DIR"

STATIC="NOT_RUN"; UNIT="NOT_RUN"; INTEGRATION="NOT_RUN"; REGRESSION="NOT_RUN"
BUILD="NOT_RUN"; STAGING="NOT_RUN"; E2E="NOT_RUN"
TOTAL_TESTS=0; PASSED=0; FAILED=0; SKIPPED=0
TOTAL_CASES=0; AUTOMATED_CASES=0; MANUAL_CASES=0; SEMI_AUTOMATED_CASES=0; CRITICAL_CASES=0; REGRESSION_CASES=0
VERIFIED_WORKING=0; WORKING_UNTESTED=0; BROKEN=0; UNKNOWN=0; BROKEN_CRITICAL=0; UNKNOWN_CRITICAL=0
EXECUTED_CASES=0; PASSED_CASES=0; FAILED_CASES=0; BLOCKED_CASES=0
NEW_TEST_CASES=0; NEW_AUTOMATED_CASES=0; NEW_MANUAL_CASES=0; NEW_SEMI_AUTOMATED_CASES=0; NEW_REGRESSION_CASES=0
PREVIOUS_TOTAL_CASES=0; PREVIOUS_AUTOMATED_CASES=0; PREVIOUS_MANUAL_CASES=0; PREVIOUS_SEMI_AUTOMATED_CASES=0; PREVIOUS_REGRESSION_CASES=0
CRITICAL_BUGS=0; NON_CRITICAL_BUGS=0
PRODUCTION="NOT_DEPLOYED"; PRODUCTION_SMOKE="NOT_RUN"; PRODUCTION_CHANGED="NO"
CURRENT_PROD_SHA="UNAVAILABLE"; ROLLBACK_SHA="UNAVAILABLE"; ROLLBACK="NOT_RUN"
VERDICT="NO_GO"
IMAGE_BUILT=0
QA_DOCKER_BUILD_NETWORK="${QA_DOCKER_BUILD_NETWORK:-}"
QA_DOCKERFILE="${QA_DOCKERFILE:-Dockerfile}"

docker_build() {
  if [ -n "$QA_DOCKER_BUILD_NETWORK" ]; then
    docker build --network="$QA_DOCKER_BUILD_NETWORK" -f "$QA_DOCKERFILE" -t "$IMAGE" .
  else
    docker build -f "$QA_DOCKERFILE" -t "$IMAGE" .
  fi
}

read_case_registry() {
  local values
  values=$("${QA_PYTHON:-python3}" - "$ROOT_DIR/qa/TEST_CASES.md" <<'PY'
import sys
rows=[]
for line in open(sys.argv[1], encoding='utf-8'):
    if not line.startswith('| TC-'):
        continue
    cells=[x.strip() for x in line.strip().strip('|').split('|')]
    if len(cells) >= 12:
        rows.append(cells)
status_map={}
for line in open(sys.argv[1], encoding='utf-8'):
    if not line.startswith('| TC-'):
        continue
    cells=[x.strip() for x in line.strip().strip('|').split('|')]
    if len(cells) == 3 and cells[1] in {'VERIFIED_WORKING','WORKING_UNTESTED','BROKEN','UNKNOWN'}:
        status_map[cells[0]]=cells[1]
total=len(rows)
automated=sum(x[5]=='AUTOMATED' for x in rows)
manual=sum(x[5]=='MANUAL' for x in rows)
critical=sum(x[3]=='CRITICAL' for x in rows)
semi=sum(x[5]=='SEMI_AUTOMATED' for x in rows)
regression=sum(x[4] in {'REGRESSION','SMOKE'} for x in rows)
verified=sum(status_map.get(x[0])=='VERIFIED_WORKING' for x in rows)
working=sum(status_map.get(x[0])=='WORKING_UNTESTED' for x in rows)
broken=sum(status_map.get(x[0])=='BROKEN' for x in rows)
unknown=sum(status_map.get(x[0])=='UNKNOWN' for x in rows)
broken_critical=sum(status_map.get(x[0])=='BROKEN' and x[3]=='CRITICAL' for x in rows)
unknown_critical=sum(status_map.get(x[0])=='UNKNOWN' and x[3]=='CRITICAL' for x in rows)
print(total, automated, manual, semi, critical, regression, verified, working, broken, unknown, broken_critical, unknown_critical)
PY
  ) || return 1
  read -r TOTAL_CASES AUTOMATED_CASES MANUAL_CASES SEMI_AUTOMATED_CASES CRITICAL_CASES REGRESSION_CASES VERIFIED_WORKING WORKING_UNTESTED BROKEN UNKNOWN BROKEN_CRITICAL UNKNOWN_CRITICAL <<EOF
$values
EOF
}
read_previous_case_counts() {
  local previous_path previous_values
  previous_path=$(ls -1t "$HISTORY_DIR"/*.md 2>/dev/null | head -1 || true)
  [ -n "$previous_path" ] || return 0
  previous_values=$("${QA_PYTHON:-python3}" - "$previous_path" <<'PY'
import re, sys
text=open(sys.argv[1], encoding="utf-8").read()
def value(name, legacy=None):
    for key in (name, legacy):
        if not key:
            continue
        match=re.search(rf"^{key} = (\d+)$", text, re.MULTILINE)
        if match:
            return match.group(1)
    return "0"
total=int(value("TOTAL_TEST_CASES"))
automated=int(value("AUTOMATED_CASES", "AUTOMATED"))
manual=int(value("MANUAL_CASES", "MANUAL"))
semi=int(value("SEMI_AUTOMATED_CASES")) or max(total-automated-manual, 0)
regression=int(value("REGRESSION_CASES")) or int(value("NEW_REGRESSION_CASES"))
print(total, automated, manual, semi, regression)
PY
  ) || return 1
  read -r PREVIOUS_TOTAL_CASES PREVIOUS_AUTOMATED_CASES PREVIOUS_MANUAL_CASES PREVIOUS_SEMI_AUTOMATED_CASES PREVIOUS_REGRESSION_CASES <<EOF
$previous_values
EOF
}
write_status() {
  cat > "$STATUS_FILE" <<EOF
RELEASE_ID = $RELEASE_ID
CANDIDATE_SHA = $CANDIDATE_SHA
PREVIOUS_GOOD_SHA = $PREVIOUS_GOOD_SHA

STATIC = $STATIC
UNIT = $UNIT
INTEGRATION = $INTEGRATION
REGRESSION = $REGRESSION

TOTAL_TESTS = $TOTAL_TESTS
PASSED = $PASSED
FAILED = $FAILED
SKIPPED = $SKIPPED

BUILD = $BUILD

STAGING = $STAGING
E2E = $E2E

CRITICAL_BUGS = $CRITICAL_BUGS
NON_CRITICAL_BUGS = $NON_CRITICAL_BUGS

QA_VERDICT = $VERDICT

PRODUCTION = $PRODUCTION
PRODUCTION_CHANGED = $PRODUCTION_CHANGED
PRODUCTION_SMOKE = $PRODUCTION_SMOKE

CURRENT_PROD_SHA = $CURRENT_PROD_SHA
ROLLBACK_SHA = $ROLLBACK_SHA
ROLLBACK = $ROLLBACK

TOTAL_TEST_CASES = $TOTAL_CASES
AUTOMATED = $AUTOMATED_CASES
MANUAL = $MANUAL_CASES
CRITICAL = $CRITICAL_CASES
AUTOMATED_CASES = $AUTOMATED_CASES
MANUAL_CASES = $MANUAL_CASES
SEMI_AUTOMATED_CASES = $SEMI_AUTOMATED_CASES
CRITICAL_CASES = $CRITICAL_CASES
REGRESSION_CASES = $REGRESSION_CASES
VERIFIED_WORKING = $VERIFIED_WORKING
WORKING_UNTESTED = $WORKING_UNTESTED
BROKEN = $BROKEN
UNKNOWN = $UNKNOWN
BROKEN_CRITICAL = $BROKEN_CRITICAL
UNKNOWN_CRITICAL = $UNKNOWN_CRITICAL
EXECUTED_THIS_RELEASE = $EXECUTED_CASES
PASSED_CASES = $PASSED_CASES
FAILED_CASES = $FAILED_CASES
BLOCKED = $BLOCKED_CASES
NEW_TEST_CASES = $NEW_TEST_CASES
NEW_AUTOMATED_CASES = $NEW_AUTOMATED_CASES
NEW_MANUAL_CASES = $NEW_MANUAL_CASES
NEW_SEMI_AUTOMATED_CASES = $NEW_SEMI_AUTOMATED_CASES
NEW_REGRESSION_CASES = $NEW_REGRESSION_CASES
EOF
}

finalize() {
  write_status
  local history_path="$HISTORY_DIR/${RELEASE_DATE}_${CANDIDATE_SHA}.md"
  if [ -e "$history_path" ]; then
    history_path="$HISTORY_DIR/${RELEASE_DATE}_${CANDIDATE_SHA}_${RELEASE_ID}.md"
  fi
  cp "$STATUS_FILE" "$history_path"
  printf 'RELEASE = %s\nNEW_FEATURE = %s\nREGRESSION = %s\nTESTS = %s/%s\nSTAGING = %s\nE2E = %s\nCRITICAL_BUGS = %s\nQA_VERDICT = %s\nPRODUCTION = %s\n' \
    "$CANDIDATE_SHA" "${NEW_FEATURE:-❌}" "${REGRESSION:-NOT_RUN}" "$PASSED" "$TOTAL_TESTS" "$STAGING" "$E2E" "$CRITICAL_BUGS" "$VERDICT" "$PRODUCTION"
}

fail_gate() { VERDICT="NO_GO"; finalize; exit 1; }
read_case_registry || { echo "test-case registry cannot be read" >&2; fail_gate; }
read_previous_case_counts || { echo "previous test-case counts cannot be read" >&2; fail_gate; }
NEW_TEST_CASES=$((TOTAL_CASES - PREVIOUS_TOTAL_CASES)); [ "$NEW_TEST_CASES" -lt 0 ] && NEW_TEST_CASES=0
NEW_AUTOMATED_CASES=$((AUTOMATED_CASES - PREVIOUS_AUTOMATED_CASES)); [ "$NEW_AUTOMATED_CASES" -lt 0 ] && NEW_AUTOMATED_CASES=0
NEW_MANUAL_CASES=$((MANUAL_CASES - PREVIOUS_MANUAL_CASES)); [ "$NEW_MANUAL_CASES" -lt 0 ] && NEW_MANUAL_CASES=0
NEW_SEMI_AUTOMATED_CASES=$((SEMI_AUTOMATED_CASES - PREVIOUS_SEMI_AUTOMATED_CASES)); [ "$NEW_SEMI_AUTOMATED_CASES" -lt 0 ] && NEW_SEMI_AUTOMATED_CASES=0
NEW_REGRESSION_CASES=$((REGRESSION_CASES - PREVIOUS_REGRESSION_CASES)); [ "$NEW_REGRESSION_CASES" -lt 0 ] && NEW_REGRESSION_CASES=0

cleanup_local_docker() {
  if [ "${QA_LOCAL_DOCKER:-0}" = "1" ] && command -v docker >/dev/null 2>&1; then
    docker compose -p "$QA_COMPOSE_PROJECT" -f "$QA_COMPOSE_FILE" down -v --remove-orphans >"$LOG_DIR/docker-down.log" 2>&1 || true
  fi
}
trap cleanup_local_docker EXIT
run_static() {
  local py="${QA_PYTHON:-python3}"
  command -v "$py" >/dev/null 2>&1 || { STATIC="FAIL"; return 1; }
  "$py" - "$ROOT_DIR" >"$LOG_DIR/static.log" 2>&1 <<'PY' || { STATIC="FAIL"; return 1; }
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = list((root / "app").rglob("*.py")) + list((root / "tests").rglob("*.py"))
for path in files:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
print(f"parsed {len(files)} Python files")
PY
  STATIC="PASS"
}

run_tests() {
  local name="$1"; shift
  local xml="$LOG_DIR/${name}.xml" log="$LOG_DIR/${name}.log"
  if [ "${QA_LOCAL_DOCKER:-0}" = "1" ]; then
    docker run --rm --entrypoint python -w /app -v "$ROOT_DIR:/app:ro" -v "$LOG_DIR:/qa-logs" "$IMAGE" -m pytest -q --junitxml="/qa-logs/${name}.xml" "$@" >"$log" 2>&1
  else
    command -v "${QA_PYTHON:-python3}" >/dev/null 2>&1 || return 1
    "${QA_PYTHON:-python3}" -m pytest -q --junitxml="$xml" "$@" >"$log" 2>&1
  fi
  local rc=$?
  if [ -f "$xml" ]; then
    local counts
    counts=$("${QA_PYTHON:-python3}" - "$xml" <<'PY'
import sys, xml.etree.ElementTree as ET
r=ET.parse(sys.argv[1]).getroot()
def n(k): return sum(int(x.attrib.get(k, 0)) for x in r.iter("testsuite"))
print(n("tests"), n("failures")+n("errors"), n("skipped"))
PY
)
    read -r t f s <<EOF
$counts
EOF
    TOTAL_TESTS=$((TOTAL_TESTS+t)); FAILED=$((FAILED+f)); SKIPPED=$((SKIPPED+s)); PASSED=$((PASSED+t-f-s))
  fi
  return "$rc"
}

run_static || fail_gate
if [ "${QA_LOCAL_DOCKER:-0}" = "1" ]; then
  if ! command -v docker >/dev/null 2>&1 || ! docker_build >"$LOG_DIR/build.log" 2>&1; then BUILD="FAIL"; fail_gate; fi
  BUILD="PASS"; IMAGE_BUILT=1
fi
run_tests unit tests -q || { UNIT="FAIL"; fail_gate; }
UNIT="PASS"
run_tests integration tests/test_demo_flows.py tests/test_scenarios.py tests/test_yandex_oauth.py tests/test_yandex_metrica.py || { INTEGRATION="FAIL"; fail_gate; }
INTEGRATION="PASS"
run_tests regression tests tests/regression || { REGRESSION="FAIL"; fail_gate; }
REGRESSION="PASS"
EXECUTED_CASES="$AUTOMATED_CASES"
PASSED_CASES="$AUTOMATED_CASES"

if [ "$IMAGE_BUILT" != "1" ]; then
  if ! command -v docker >/dev/null 2>&1 || ! docker_build >"$LOG_DIR/build.log" 2>&1; then BUILD="FAIL"; fail_gate; fi
  BUILD="PASS"
fi

if [ "${QA_LOCAL_DOCKER:-0}" = "1" ]; then
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >"$LOG_DIR/docker-compose-version.log" 2>&1; then
    STAGING="FAIL"; fail_gate
  fi
  if [ -n "${QA_STAGING_IMAGE:-}" ]; then
    staging_up_args=(up -d --no-build)
  else
    staging_up_args=(up -d --build)
  fi
  if ! docker compose -p "$QA_COMPOSE_PROJECT" -f "$QA_COMPOSE_FILE" "${staging_up_args[@]}" >"$LOG_DIR/staging-up.log" 2>&1; then
    STAGING="FAIL"; fail_gate
  fi
  QA_STAGING_URL="${QA_STAGING_URL:-http://127.0.0.1:$QA_STAGING_PORT}"
fi
if [ -z "${QA_STAGING_URL:-}" ]; then
  STAGING="NOT_RUN"; E2E="NOT_RUN"; fail_gate
fi
ready=0
for attempt in $(seq 1 "${QA_HEALTH_RETRIES:-30}"); do
  if curl --fail --silent --show-error --max-time "${QA_HTTP_TIMEOUT:-15}" "$QA_STAGING_URL/health/ready" >"$LOG_DIR/staging-health.json" 2>"$LOG_DIR/staging-health.log"; then ready=1; break; fi
  sleep "${QA_HEALTH_INTERVAL:-2}"
done
if [ "$ready" != "1" ]; then STAGING="FAIL"; fail_gate; fi
STAGING="PASS"
if [ "${QA_LOCAL_DOCKER:-0}" = "1" ] && [ -z "${QA_STAGING_E2E_CMD:-}" ]; then
  QA_STAGING_E2E_CMD="curl --fail --silent --show-error $QA_STAGING_URL/health/live && curl --fail --silent --show-error $QA_STAGING_URL/mini && curl --fail --silent --show-error $QA_STAGING_URL/traffic-test/app"
fi
if [ -z "${QA_STAGING_E2E_CMD:-}" ]; then E2E="NOT_RUN"; fail_gate; fi
if ! bash -o pipefail -c "$QA_STAGING_E2E_CMD" >"$LOG_DIR/staging-e2e.log" 2>&1; then E2E="FAIL"; fail_gate; fi
E2E="PASS"
if [ "$BROKEN_CRITICAL" -gt 0 ] || [ "$UNKNOWN_CRITICAL" -gt 0 ]; then
  VERDICT="NO_GO"
  finalize
  exit 1
fi
NEW_FEATURE="✅"

VERDICT="GO"
PRODUCTION="WAITING_OWNER_APPROVAL"
write_status

if [ "${1:-}" = "DEPLOY" ]; then
  if [ "${OWNER_APPROVED:-0}" != "1" ] || [ "${VERDICT}" != "GO" ] || [ -z "${PRODUCTION_DEPLOY_CMD:-}" ]; then
    echo "DEPLOY BLOCKED: require QA GO, OWNER_APPROVED=1 and PRODUCTION_DEPLOY_CMD" >&2
    fail_gate
  fi
  PRODUCTION="DEPLOYED"; PRODUCTION_CHANGED="YES"; CURRENT_PROD_SHA="$CANDIDATE_SHA"
  if ! bash -o pipefail -c "$PRODUCTION_DEPLOY_CMD" >"$LOG_DIR/production-deploy.log" 2>&1; then PRODUCTION_SMOKE="FAIL"; fail_gate; fi
  if [ -z "${PRODUCTION_URL:-}" ] || ! curl --fail --silent --show-error --max-time "${QA_HTTP_TIMEOUT:-15}" "$PRODUCTION_URL/health/ready" >"$LOG_DIR/production-smoke.json" 2>"$LOG_DIR/production-smoke.log"; then
    PRODUCTION_SMOKE="FAIL"
    ROLLBACK_SHA="$PREVIOUS_GOOD_SHA"
    if [ -n "${PRODUCTION_ROLLBACK_CMD:-}" ] && bash -o pipefail -c "$PRODUCTION_ROLLBACK_CMD" >>"$LOG_DIR/rollback.log" 2>&1; then ROLLBACK="PASS"; else ROLLBACK="FAIL"; fi
    fail_gate
  fi
  PRODUCTION_SMOKE="PASS"
fi
finalize
exit 0
