#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Scheduled by systemd. Uses the installed AWS CLI, age, Docker and PostgreSQL
# utilities; it is deliberately not part of the application release workflow.

WORK_DIR=""
RESTORE_CONTAINER=""
TEMP_ROOT="${MAXIBOT_BUNNY_TEMP_ROOT:-/var/tmp}"
S3_BUCKET="${BUNNY_S3_BUCKET:-maxibot-backups}"
S3_ENDPOINT="${BUNNY_S3_ENDPOINT:-https://de-s3.storage.bunnycdn.com}"
S3_REGION="${BUNNY_S3_REGION:-de}"
S3_PREFIX="${BUNNY_S3_PREFIX:-maxibot/daily}"
RESTORE_MARKER_KEY="${BUNNY_RESTORE_MARKER_KEY:-maxibot/control/last-restore-tested.json}"
DB_CONTAINER="${MAXIBOT_DB_CONTAINER:-maxibot-vps-db-1}"
DB_NAME="${MAXIBOT_DB_NAME:-maxibot}"
UPLOADS_VOLUME="${MAXIBOT_UPLOADS_VOLUME:-maxibot-vps_maxibot_uploads}"
RETENTION_DAYS="${BUNNY_RETENTION_DAYS:-30}"
START_SECONDS=0

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }
fail() { log "ERROR: $*" >&2; return 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command unavailable: $1"
}

validate_config() {
  [[ "$S3_BUCKET" == maxibot-backups ]] || fail "unexpected Bunny bucket"
  [[ "$S3_ENDPOINT" == https://de-s3.storage.bunnycdn.com ]] || fail "unexpected Bunny endpoint"
  [[ "$S3_REGION" == de ]] || fail "unexpected Bunny region"
  [[ "$S3_PREFIX" =~ ^[a-z0-9/-]+$ && "$S3_PREFIX" != */../* ]] || fail "invalid object prefix"
  [[ "$RESTORE_MARKER_KEY" == maxibot/control/last-restore-tested.json ]] || fail "unexpected restore marker key"
  [[ "$DB_CONTAINER" == maxibot-vps-db-1 && "$DB_NAME" == maxibot ]] || fail "unexpected PostgreSQL target"
  [[ "$UPLOADS_VOLUME" == maxibot-vps_maxibot_uploads ]] || fail "unexpected uploads volume"
  [[ "$RETENTION_DAYS" =~ ^[0-9]+$ && "$RETENTION_DAYS" -eq 30 ]] || fail "retention must remain 30 days"
  [[ "${BUNNY_AGE_RECIPIENT:-}" =~ ^age1[0-9a-z]+$ ]] || fail "BUNNY_AGE_RECIPIENT is missing or invalid"
  [[ -n "${CREDENTIALS_DIRECTORY:-}" && -r "$CREDENTIALS_DIRECTORY/bunny-s3" ]] || fail "systemd Bunny credential is unavailable"
}

prepare_workspace() {
  WORK_DIR="$(mktemp -d "$TEMP_ROOT/maxibot-bunny.XXXXXX")" || fail "cannot create private temporary directory"
  chmod 700 "$WORK_DIR" || fail "cannot secure temporary directory"
  cat > "$WORK_DIR/aws-config" <<EOF
[default]
region = $S3_REGION
s3 =
    addressing_style = path
EOF
  chmod 600 "$WORK_DIR/aws-config"
}

aws_s3() {
  AWS_SHARED_CREDENTIALS_FILE="$CREDENTIALS_DIRECTORY/bunny-s3" \
  AWS_CONFIG_FILE="$WORK_DIR/aws-config" \
  AWS_EC2_METADATA_DISABLED=true \
    aws --no-cli-pager --endpoint-url "$S3_ENDPOINT" --region "$S3_REGION" "$@"
}

sha256_file() { sha256sum "$1" | awk '{print $1}'; }
is_pinned_postgres_image() {
  [[ "$1" =~ ^(docker\.io/)?(library/)?postgres(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$ ]]
}

# The HeadObject size/metadata and a full GET readback must all agree. Bunny's
# multipart ETag is not treated as a checksum.
verify_uploaded_archive() {
  local key="$1" file="$2" expected_sha="$3" expected_size="$4"
  local head_size metadata_sha remote_sha uri
  uri="s3://$S3_BUCKET/$key"
  head_size="$(aws_s3 s3api head-object --bucket "$S3_BUCKET" --key "$key" --query ContentLength --output text)" || return 1
  metadata_sha="$(aws_s3 s3api head-object --bucket "$S3_BUCKET" --key "$key" --query Metadata.sha256 --output text)" || return 1
  [[ "$head_size" =~ ^[0-9]+$ && "$head_size" == "$expected_size" ]] || return 1
  [[ "$metadata_sha" == "$expected_sha" && "$(sha256_file "$file")" == "$expected_sha" ]] || return 1
  remote_sha="$(aws_s3 s3 cp "$uri" - --only-show-errors | sha256sum | awk '{print $1}')" || return 1
  [[ "$remote_sha" == "$expected_sha" ]]
}

cleanup_restore_container() {
  local name="${1:-}" inspect_err cleanup_status=0
  [[ -n "$name" ]] || return 0
  inspect_err="$(mktemp "${TMPDIR:-/tmp}/maxibot-docker-inspect.XXXXXX")" || return 1
  if docker inspect "$name" >/dev/null 2>"$inspect_err"; then
    if ! docker rm -f "$name" >/dev/null 2>&1; then
      log "ERROR: failed to remove isolated restore container" >&2
      cleanup_status=1
    fi
  elif ! grep -Fq 'No such object:' "$inspect_err"; then
    # A missing container is benign; a daemon/API error is not absence.
    log "ERROR: Docker could not confirm isolated restore container state" >&2
    cleanup_status=1
  fi
  rm -f -- "$inspect_err" || cleanup_status=1
  return "$cleanup_status"
}

cleanup_sensitive_workdir() {
  [[ -z "$WORK_DIR" || ! -d "$WORK_DIR" ]] && return 0
  rm -rf -- "$WORK_DIR"
}

cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n "$RESTORE_CONTAINER" ]]; then
    cleanup_restore_container "$RESTORE_CONTAINER" || status=1
  fi
  cleanup_sensitive_workdir || { log "ERROR: failed to remove temporary backup/restore data" >&2; status=1; }
  exit "$status"
}

get_restore_marker() {
  local marker_file="$1" err_file="$2"
  if ! aws_s3 s3api head-object --bucket "$S3_BUCKET" --key "$RESTORE_MARKER_KEY" >/dev/null 2>"$err_file"; then
    if grep -Eq '\(404\)|NoSuchKey|Not Found' "$err_file"; then
      return 2
    fi
    log "ERROR: cannot read Bunny restore-tested marker" >&2
    return 1
  fi
  aws_s3 s3 cp "s3://$S3_BUCKET/$RESTORE_MARKER_KEY" "$marker_file" --only-show-errors || return 1
  python3 - "$marker_file" "$S3_PREFIX/" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    marker = json.load(stream)
key, digest = marker.get("key", ""), marker.get("sha256", "")
if not key.startswith(sys.argv[2]) or not key.endswith(".tar.age"):
    raise SystemExit("invalid restore-tested object key")
if not re.fullmatch(re.escape(sys.argv[2]) + r"[0-9]{8}T[0-9]{6}Z-[0-9]+\.tar\.age", key):
    raise SystemExit("invalid restore-tested object name")
if not re.fullmatch(r"[0-9a-f]{64}", digest):
    raise SystemExit("invalid restore-tested SHA-256")
print(key)
print(digest)
PY
}

prune_expired_backups() {
  local marker_file="$WORK_DIR/restore-tested.json" marker_err="$WORK_DIR/marker.err"
  local marker_key marker_sha list_file cutoff now key head_sha marker_status
  if get_restore_marker "$marker_file" "$marker_err" > "$WORK_DIR/marker.values"; then
    marker_status=0
  else
    marker_status=$?
  fi
  if (( marker_status == 2 )); then
    log "INFO: retention deferred until the first successful Bunny restore drill"
    return 0
  elif (( marker_status != 0 )); then
    return 1
  fi
  marker_key="$(sed -n '1p' "$WORK_DIR/marker.values")"
  marker_sha="$(sed -n '2p' "$WORK_DIR/marker.values")"
  [[ -n "$marker_key" && "$marker_sha" =~ ^[0-9a-f]{64}$ ]] || return 1
  head_sha="$(aws_s3 s3api head-object --bucket "$S3_BUCKET" --key "$marker_key" --query Metadata.sha256 --output text)" || return 1
  [[ "$head_sha" == "$marker_sha" ]] || fail "restore-tested Bunny object no longer matches its verified digest"
  head_sha="$(aws_s3 s3 cp "s3://$S3_BUCKET/$marker_key" - --only-show-errors | sha256sum | awk '{print $1}')" || return 1
  [[ "$head_sha" == "$marker_sha" ]] || fail "restore-tested Bunny object body no longer matches its verified digest"

  list_file="$WORK_DIR/objects.json"
  aws_s3 s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "$S3_PREFIX/" --output json > "$list_file" || return 1
  now="$(date -u +%s)"; cutoff=$((now - RETENTION_DAYS * 86400))
  python3 - "$list_file" "$cutoff" "$marker_key" "$S3_PREFIX/" > "$WORK_DIR/expired.keys" <<'PY'
import datetime, json, re, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)
if result.get("IsTruncated"):
    raise SystemExit("S3 list exceeded one page; refusing partial retention deletion")
cutoff, protected, prefix = int(sys.argv[2]), sys.argv[3], sys.argv[4]
for item in result.get("Contents", []):
    key = item.get("Key", "")
    if not re.fullmatch(re.escape(prefix) + r"[0-9]{8}T[0-9]{6}Z-[0-9]+\.tar\.age", key) or key == protected:
        continue
    modified = datetime.datetime.fromisoformat(item["LastModified"].replace("Z", "+00:00"))
    if modified.timestamp() < cutoff:
        print(key)
PY
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    aws_s3 s3api delete-object --bucket "$S3_BUCKET" --key "$key" >/dev/null || return 1
    log "INFO: removed Bunny archive outside ${RETENTION_DAYS}-day retention"
  done < "$WORK_DIR/expired.keys"
}

run_backup() {
  local stamp db_head_before db_head_after db_bytes uploads_bytes db_image_id db_image_digest
  local db_dump_bytes globals_dump_bytes uploads_dump_bytes
  local db_extensions_before db_extensions_after db_tables_before db_tables_after
  local uploads_container archive key archive_sha archive_size free_bytes needed_bytes
  local db_dump="$WORK_DIR/maxibot.dump" globals_dump="$WORK_DIR/globals.sql"
  local uploads_dump="$WORK_DIR/uploads.tar.gz" payload="$WORK_DIR/payload"
  mkdir -m 700 "$payload"

  docker inspect "$DB_CONTAINER" --format '{{.State.Running}}' | grep -Fxq true || fail "PostgreSQL container is not running"
  db_image_id="$(docker inspect "$DB_CONTAINER" --format '{{.Image}}')" || fail "cannot inspect PostgreSQL image identity"
  [[ "$db_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "invalid PostgreSQL image identity"
  db_image_digest="$(docker image inspect "$db_image_id" --format '{{range .RepoDigests}}{{println .}}{{end}}' | head -n 1)" || fail "cannot inspect PostgreSQL registry digest"
  is_pinned_postgres_image "$db_image_digest" || fail "PostgreSQL image lacks an immutable registry digest"
  db_head_before="$(docker exec "$DB_CONTAINER" psql -X -U maxibot -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c 'select version_num from alembic_version')" || fail "cannot read PostgreSQL Alembic head"
  [[ "$db_head_before" =~ ^[A-Za-z0-9_]+$ ]] || fail "unexpected PostgreSQL Alembic head"
  db_bytes="$(docker exec "$DB_CONTAINER" psql -X -U maxibot -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c "select pg_database_size('$DB_NAME')")" || fail "cannot read PostgreSQL database size"
  [[ "$db_bytes" =~ ^[0-9]+$ ]] || fail "invalid PostgreSQL database size"
  db_extensions_before="$(docker exec "$DB_CONTAINER" psql -X -U maxibot -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c "select coalesce(string_agg(extname, ',' order by extname), '') from pg_extension")" || fail "cannot read PostgreSQL extensions"
  [[ "$db_extensions_before" =~ ^[A-Za-z0-9_,]*$ ]] || fail "invalid PostgreSQL extension list"
  db_tables_before="$(docker exec "$DB_CONTAINER" psql -X -U maxibot -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")" || fail "cannot read PostgreSQL table count"
  [[ "$db_tables_before" =~ ^[0-9]+$ && "$db_tables_before" -gt 0 ]] || fail "invalid PostgreSQL table count"
  uploads_container="$(docker ps --no-trunc -q --filter "volume=$UPLOADS_VOLUME" | head -n 1)" || fail "cannot locate uploads volume consumer"
  [[ "$uploads_container" =~ ^[0-9a-f]{64}$ ]] || fail "uploads volume is not attached to a running API container"
  local api_image_id
  api_image_id="$(docker inspect "$uploads_container" --format '{{.Image}}')" || fail "cannot inspect uploads volume consumer"
  [[ "$api_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "invalid uploads reader image identity"
  uploads_bytes="$(docker run --rm --pull=never --network none --read-only \
    --mount "type=volume,source=$UPLOADS_VOLUME,target=/source,readonly" \
    --entrypoint python "$api_image_id" -c 'import os; print(sum(os.path.getsize(os.path.join(root, name)) for root, _, files in os.walk("/source") for name in files if not os.path.islink(os.path.join(root, name))))')" || fail "cannot measure uploads volume"
  [[ "$uploads_bytes" =~ ^[0-9]+$ ]] || fail "invalid uploads volume size"
  free_bytes="$(df -PB1 "$TEMP_ROOT" | awk 'NR == 2 {print $4}')" || fail "cannot check temporary disk space"
  [[ "$free_bytes" =~ ^[0-9]+$ ]] || fail "invalid free disk space result"
  needed_bytes=$((2 * db_bytes + 2 * uploads_bytes + 1073741824))
  (( free_bytes >= needed_bytes )) || fail "insufficient free disk for a private backup workspace"

  log "INFO: creating PostgreSQL custom dump and globals snapshot"
  docker exec "$DB_CONTAINER" pg_dumpall -U maxibot --globals-only > "$globals_dump" || fail "PostgreSQL global roles dump failed"
  docker exec "$DB_CONTAINER" pg_dump -U maxibot -d "$DB_NAME" --format=custom > "$db_dump" || fail "PostgreSQL database dump failed"
  chmod 600 "$globals_dump" "$db_dump"
  test -s "$globals_dump" && test -s "$db_dump" || fail "PostgreSQL backup output is empty"
  docker exec -i "$DB_CONTAINER" pg_restore --list < "$db_dump" >/dev/null || fail "PostgreSQL custom archive validation failed"
  docker run --rm --pull=never --network none --read-only \
    --mount "type=volume,source=$UPLOADS_VOLUME,target=/source,readonly" \
    --entrypoint python "$api_image_id" -c 'import os,sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz"); t.add("/source", arcname="uploads", recursive=True); t.close()' \
    > "$uploads_dump" || fail "uploads archive creation failed"
  chmod 600 "$uploads_dump"
  test -s "$uploads_dump" || fail "uploads backup output is empty"
  validate_archive_paths "$uploads_dump" || fail "uploads volume contains unsafe links or special files"
  db_dump_bytes="$(wc -c < "$db_dump" | tr -d '[:space:]')"
  globals_dump_bytes="$(wc -c < "$globals_dump" | tr -d '[:space:]')"
  uploads_dump_bytes="$(wc -c < "$uploads_dump" | tr -d '[:space:]')"
  [[ "$db_dump_bytes" =~ ^[0-9]+$ && "$globals_dump_bytes" =~ ^[0-9]+$ && "$uploads_dump_bytes" =~ ^[0-9]+$ ]] || fail "invalid dump sizes"
  free_bytes="$(df -PB1 "$TEMP_ROOT" | awk 'NR == 2 {print $4}')" || fail "cannot recheck temporary disk space"
  needed_bytes=$((2 * (db_dump_bytes + globals_dump_bytes + uploads_dump_bytes) + 1073741824))
  (( free_bytes >= needed_bytes )) || fail "insufficient free disk for payload copy and encrypted archive"
  db_head_after="$(docker exec "$DB_CONTAINER" psql -X -U maxibot -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c 'select version_num from alembic_version')" || fail "cannot re-read PostgreSQL Alembic head"
  [[ "$db_head_after" == "$db_head_before" ]] || fail "Alembic head changed while the backup was running"
  db_extensions_after="$(docker exec "$DB_CONTAINER" psql -X -U maxibot -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c "select coalesce(string_agg(extname, ',' order by extname), '') from pg_extension")" || fail "cannot re-read PostgreSQL extensions"
  [[ "$db_extensions_after" == "$db_extensions_before" ]] || fail "PostgreSQL extensions changed while the backup was running"
  db_tables_after="$(docker exec "$DB_CONTAINER" psql -X -U maxibot -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")" || fail "cannot re-read PostgreSQL table count"
  [[ "$db_tables_after" == "$db_tables_before" ]] || fail "PostgreSQL table catalog changed while the backup was running"

  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  cat > "$payload/manifest.txt" <<EOF
format=1
created_at=$stamp
database=$DB_NAME
database_head=$db_head_before
database_bytes=$db_bytes
database_extensions=$db_extensions_before
database_tables=$db_tables_before
database_image=$db_image_digest
uploads_bytes=$uploads_bytes
uploads_archive_sha256=$(sha256_file "$uploads_dump")
EOF
  cp -- "$db_dump" "$globals_dump" "$uploads_dump" "$payload/"
  archive="$WORK_DIR/maxibot-$stamp.tar.age"
  tar -C "$payload" -czf - . | age --recipient "$BUNNY_AGE_RECIPIENT" --output "$archive" - || fail "client-side age encryption failed"
  chmod 600 "$archive"
  archive_sha="$(sha256_file "$archive")" || fail "cannot hash encrypted archive"
  archive_size="$(wc -c < "$archive" | tr -d '[:space:]')"
  [[ "$archive_size" =~ ^[0-9]+$ && "$archive_size" -gt 0 ]] || fail "encrypted archive is empty"
  key="$S3_PREFIX/$stamp.tar.age"

  log "INFO: uploading encrypted backup to private Bunny S3"
  aws_s3 s3 cp "$archive" "s3://$S3_BUCKET/$key" --only-show-errors \
    --metadata "sha256=$archive_sha,database-bytes=$db_bytes,uploads-bytes=$uploads_bytes,database-head=$db_head_before" || fail "Bunny S3 upload failed"
  verify_uploaded_archive "$key" "$archive" "$archive_sha" "$archive_size" || fail "Bunny S3 object readback/checksum verification failed"
  log "PASS: encrypted Bunny backup uploaded and verified; key=$key; elapsed_seconds=$((SECONDS - START_SECONDS))"
  prune_expired_backups || fail "Bunny retention step failed; no local recovery copy was removed"
}

extract_manifest_value() {
  local file="$1" key="$2"
  awk -F= -v wanted="$key" '$1 == wanted {print substr($0, index($0, "=") + 1)}' "$file"
}

validate_archive_paths() {
  python3 - "$1" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as archive:
    seen = set()
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("unsafe path in encrypted backup")
        if member.name in seen:
            raise SystemExit("duplicate path in encrypted backup")
        seen.add(member.name)
        if not (member.isdir() or member.isfile()):
            raise SystemExit("links and special files are not allowed in encrypted backup")
PY
}

publish_restore_marker() {
  local key="$1" digest="$2" marker="$WORK_DIR/restore-tested.json" marker_readback="$WORK_DIR/restore-tested.readback"
  python3 - "$marker" "$key" "$digest" <<'PY'
import datetime, json, sys
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"key": sys.argv[2], "sha256": sys.argv[3], "tested_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, stream, sort_keys=True)
    stream.write("\n")
PY
  if ! aws_s3 s3 cp "$marker" "s3://$S3_BUCKET/$RESTORE_MARKER_KEY" --only-show-errors; then
    # The PUT response may be lost. Read back before reporting failure.
    aws_s3 s3 cp "s3://$S3_BUCKET/$RESTORE_MARKER_KEY" "$marker_readback" --only-show-errors || return 1
    cmp -s "$marker" "$marker_readback" || return 1
  else
    aws_s3 s3 cp "s3://$S3_BUCKET/$RESTORE_MARKER_KEY" "$marker_readback" --only-show-errors || return 1
    cmp -s "$marker" "$marker_readback" || return 1
  fi
}

run_restore_drill() {
  local list_file="$WORK_DIR/objects.json" latest_key archive="$WORK_DIR/restore.tar.age"
  local head_file="$WORK_DIR/head.json" expected_sha expected_size remote_sha
  local db_image db_head db_bytes uploads_bytes uploads_sha restore_data uploads_restore
  local restore_name stamp restored_head restored_extensions restored_tables restored_uploads_bytes
  aws_s3 s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "$S3_PREFIX/" --output json > "$list_file" || fail "cannot list Bunny backups"
  latest_key="$(python3 - "$list_file" "$S3_PREFIX/" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)
if result.get("IsTruncated"):
    raise SystemExit("S3 list exceeded one page; refusing ambiguous restore selection")
prefix = sys.argv[2]
keys = [item for item in result.get("Contents", []) if re.fullmatch(re.escape(prefix) + r"[0-9]{8}T[0-9]{6}Z-[0-9]+\.tar\.age", item.get("Key", ""))]
if not keys:
    raise SystemExit("no verified Bunny backup is available for restore drill")
print(max(keys, key=lambda item: item.get("LastModified", ""))["Key"])
PY
  )" || fail "cannot select latest Bunny backup"
  aws_s3 s3api head-object --bucket "$S3_BUCKET" --key "$latest_key" --output json > "$head_file" || fail "cannot inspect latest Bunny backup"
  local head_values
  head_values="$(python3 - "$head_file" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    head = json.load(stream)
metadata = head.get("Metadata", {})
values = [str(head.get("ContentLength", "")), metadata.get("sha256", ""), metadata.get("database-bytes", ""), metadata.get("uploads-bytes", ""), metadata.get("database-head", "")]
if not re.fullmatch(r"[0-9]+", values[0]) or not re.fullmatch(r"[0-9a-f]{64}", values[1]):
    raise SystemExit("invalid Bunny backup size or SHA-256 metadata")
for value in values[2:4]:
    if not re.fullmatch(r"[0-9]+", value):
        raise SystemExit("invalid Bunny backup size metadata")
if not re.fullmatch(r"[A-Za-z0-9_]+", values[4]):
    raise SystemExit("invalid Bunny Alembic head metadata")
print("\n".join(values))
PY
  )" || fail "invalid Bunny backup metadata"
  [[ "$(printf '%s\n' "$head_values" | wc -l | tr -d ' ')" -eq 5 ]] || fail "incomplete Bunny backup metadata"
  expected_size="$(printf '%s\n' "$head_values" | sed -n '1p')"
  expected_sha="$(printf '%s\n' "$head_values" | sed -n '2p')"
  db_bytes="$(printf '%s\n' "$head_values" | sed -n '3p')"
  uploads_bytes="$(printf '%s\n' "$head_values" | sed -n '4p')"
  db_head="$(printf '%s\n' "$head_values" | sed -n '5p')"
  local free_bytes needed_bytes
  free_bytes="$(df -PB1 "$TEMP_ROOT" | awk 'NR == 2 {print $4}')" || fail "cannot check restore disk space"
  [[ "$free_bytes" =~ ^[0-9]+$ ]] || fail "invalid restore free space result"
  needed_bytes=$((3 * expected_size + 2 * db_bytes + 2 * uploads_bytes + 1073741824))
  (( free_bytes >= needed_bytes )) || fail "insufficient free disk for isolated Bunny restore"
  aws_s3 s3 cp "s3://$S3_BUCKET/$latest_key" "$archive" --only-show-errors || fail "cannot download Bunny backup"
  [[ "$(wc -c < "$archive" | tr -d '[:space:]')" == "$expected_size" ]] || fail "downloaded Bunny archive size mismatch"
  [[ "$(sha256_file "$archive")" == "$expected_sha" ]] || fail "downloaded Bunny archive SHA-256 mismatch"

  local identity="${CREDENTIALS_DIRECTORY}/backup-age-identity"
  [[ -r "$identity" ]] || fail "systemd age identity credential is unavailable"
  age --decrypt --identity "$identity" --output "$WORK_DIR/restore.tar.gz" "$archive" || fail "Bunny archive decryption failed"
  validate_archive_paths "$WORK_DIR/restore.tar.gz" || fail "unsafe encrypted backup archive paths"
  mkdir -m 700 "$WORK_DIR/restored"
  tar -C "$WORK_DIR/restored" -xzf "$WORK_DIR/restore.tar.gz" --no-same-owner --no-same-permissions || fail "Bunny backup archive extraction failed"
  local manifest="$WORK_DIR/restored/manifest.txt"
  [[ -s "$manifest" && -s "$WORK_DIR/restored/maxibot.dump" && -s "$WORK_DIR/restored/globals.sql" && -s "$WORK_DIR/restored/uploads.tar.gz" ]] || fail "Bunny backup payload is incomplete"
  [[ "$(extract_manifest_value "$manifest" database_head)" == "$db_head" ]] || fail "backup Alembic head does not match S3 metadata"
  [[ "$(extract_manifest_value "$manifest" database_bytes)" == "$db_bytes" ]] || fail "backup database size metadata mismatch"
  [[ "$(extract_manifest_value "$manifest" uploads_bytes)" == "$uploads_bytes" ]] || fail "backup uploads size metadata mismatch"
  uploads_sha="$(extract_manifest_value "$manifest" uploads_archive_sha256)"
  [[ "$(sha256_file "$WORK_DIR/restored/uploads.tar.gz")" == "$uploads_sha" ]] || fail "uploads archive checksum mismatch"
  db_image="$(extract_manifest_value "$manifest" database_image)"
  is_pinned_postgres_image "$db_image" || fail "backup PostgreSQL image digest is invalid"
  docker image inspect "$db_image" >/dev/null 2>&1 || docker pull "$db_image" >/dev/null || fail "cannot obtain pinned PostgreSQL image for restore"
  docker exec -i "$DB_CONTAINER" pg_restore --list < "$WORK_DIR/restored/maxibot.dump" >/dev/null || fail "Bunny PostgreSQL archive failed pg_restore list"

  restore_data="$WORK_DIR/pgdata"; uploads_restore="$WORK_DIR/uploads-restore"
  mkdir -m 700 "$restore_data" "$uploads_restore"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"; restore_name="maxibot-bunny-restore-${stamp}-$$"
  [[ ! "$restore_name" =~ [^A-Za-z0-9_.-] ]] || fail "invalid isolated restore container name"
  RESTORE_CONTAINER="$restore_name"
  docker run -d --name "$restore_name" --network none \
    --mount "type=bind,source=$restore_data,target=/var/lib/postgresql/data" \
    -e POSTGRES_USER=restore_admin -e POSTGRES_DB=postgres -e POSTGRES_HOST_AUTH_METHOD=trust \
    "$db_image" >/dev/null || fail "cannot start isolated PostgreSQL restore container"
  local ready=0
  for _ in $(seq 1 90); do
    if docker exec "$restore_name" pg_isready -U restore_admin -d postgres >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  (( ready == 1 )) || fail "isolated PostgreSQL did not become ready"
  docker exec -i "$restore_name" psql -X -U restore_admin -d postgres -v ON_ERROR_STOP=1 < "$WORK_DIR/restored/globals.sql" >/dev/null || fail "PostgreSQL global roles restore failed"
  docker cp "$WORK_DIR/restored/maxibot.dump" "$restore_name:/tmp/maxibot.dump" >/dev/null || fail "cannot stage database dump in isolated restore container"
  docker exec "$restore_name" pg_restore --create --exit-on-error -U restore_admin -d postgres /tmp/maxibot.dump >/dev/null || fail "isolated PostgreSQL restore failed"
  restored_head="$(docker exec "$restore_name" psql -X -U restore_admin -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c 'select version_num from alembic_version')" || fail "cannot read restored Alembic head"
  [[ "$restored_head" == "$db_head" ]] || fail "restored Alembic head mismatch"
  restored_tables="$(docker exec "$restore_name" psql -X -U restore_admin -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")" || fail "cannot inspect restored tables"
  [[ "$restored_tables" =~ ^[0-9]+$ && "$restored_tables" -gt 0 && "$restored_tables" == "$(extract_manifest_value "$manifest" database_tables)" ]] || fail "restored database public table count mismatch"
  restored_extensions="$(docker exec "$restore_name" psql -X -U restore_admin -d "$DB_NAME" -At -v ON_ERROR_STOP=1 -c "select coalesce(string_agg(extname, ',' order by extname), '') from pg_extension")" || fail "cannot inspect restored extensions"
  [[ "$restored_extensions" == "$(extract_manifest_value "$manifest" database_extensions)" ]] || fail "restored PostgreSQL extensions mismatch"
  docker stop "$restore_name" >/dev/null || fail "cannot stop isolated restore container"
  cleanup_restore_container "$restore_name" || fail "cannot clean isolated restore container"
  RESTORE_CONTAINER=""

  validate_archive_paths "$WORK_DIR/restored/uploads.tar.gz" || fail "unsafe uploads archive paths"
  tar -C "$uploads_restore" -xzf "$WORK_DIR/restored/uploads.tar.gz" --no-same-owner --no-same-permissions || fail "isolated uploads restore failed"
  restored_uploads_bytes="$(python3 - "$uploads_restore" <<'PY'
import os, sys
root = sys.argv[1]
print(sum(os.path.getsize(os.path.join(path, name)) for path, _, files in os.walk(root) for name in files if not os.path.islink(os.path.join(path, name))))
PY
  )" || fail "cannot verify restored uploads size"
  [[ "$restored_uploads_bytes" == "$uploads_bytes" ]] || fail "restored uploads size mismatch"
  publish_restore_marker "$latest_key" "$expected_sha" || fail "cannot publish verified Bunny restore marker"
  log "PASS: isolated Bunny S3 PostgreSQL and uploads restore; head=$restored_head; tables=$restored_tables; elapsed_seconds=$((SECONDS - START_SECONDS))"
  prune_expired_backups || fail "Bunny retention cleanup failed after restore drill"
}

main() {
  [[ $# -eq 1 && ( "$1" == backup || "$1" == restore-drill ) ]] || fail "usage: maxibot-bunny-backup.sh {backup|restore-drill}"
  START_SECONDS=$SECONDS
  for command in aws age docker python3 tar sha256sum awk df; do require_command "$command"; done
  validate_config
  trap cleanup EXIT
  prepare_workspace
  if [[ "$1" == backup ]]; then run_backup; else
    [[ -r "$CREDENTIALS_DIRECTORY/backup-age-identity" ]] || fail "systemd age identity credential is unavailable"
    run_restore_drill
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
