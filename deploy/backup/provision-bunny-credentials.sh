#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ "$(id -u)" != 0 ]]; then
  echo "Run this on the VPS as root through its approved secure console." >&2
  exit 1
fi
command -v systemd-creds >/dev/null 2>&1 || { echo "systemd-creds is required." >&2; exit 1; }
install -d -o root -g root -m 0700 /etc/credstore.encrypted

set +x
store_credential() {
  local name="$1" destination="$2" temporary
  if [[ -e "$destination" ]]; then
    echo "Existing encrypted credential left unchanged: $name"
    return 0
  fi
  temporary="$(mktemp "/etc/credstore.encrypted/.${name}.XXXXXX")" || return 1
  if ! systemd-creds encrypt --with-key=host --name="$name" - "$temporary"; then
    rm -f -- "$temporary"
    echo "Could not encrypt credential: $name" >&2
    return 1
  fi
  chown root:root "$temporary" && chmod 0600 "$temporary" || {
    rm -f -- "$temporary"
    echo "Could not secure encrypted credential: $name" >&2
    return 1
  }
  if ! ln "$temporary" "$destination"; then
    rm -f -- "$temporary"
    echo "Credential destination already exists or cannot be installed: $name" >&2
    return 1
  fi
  rm -f -- "$temporary"
}

if [[ ! -e /etc/credstore.encrypted/maxibot-bunny-s3 ]]; then
  read -r -s -p "Bunny Storage Zone Secret Access Key: " BUNNY_SECRET
  printf '\n'
  [[ -n "$BUNNY_SECRET" ]] || { unset BUNNY_SECRET; echo "Empty Bunny secret rejected." >&2; exit 1; }
  if ! {
    printf '[default]\naws_access_key_id = maxibot-backups\naws_secret_access_key = %s\n' "$BUNNY_SECRET"
  } | store_credential bunny-s3 /etc/credstore.encrypted/maxibot-bunny-s3; then
    unset BUNNY_SECRET
    exit 1
  fi
  unset BUNNY_SECRET
else
  echo "Existing encrypted credential left unchanged: bunny-s3"
fi

if [[ ! -e /etc/credstore.encrypted/maxibot-backup-age-identity ]]; then
  read -r -s -p "age private identity from the approved password manager: " AGE_IDENTITY
  printf '\n'
  [[ "$AGE_IDENTITY" =~ ^AGE-SECRET-KEY-1[0-9A-Z]+$ ]] || { unset AGE_IDENTITY; echo "Invalid age identity format." >&2; exit 1; }
  if ! printf '%s\n' "$AGE_IDENTITY" | store_credential backup-age-identity /etc/credstore.encrypted/maxibot-backup-age-identity; then
    unset AGE_IDENTITY
    exit 1
  fi
  unset AGE_IDENTITY
else
  echo "Existing encrypted credential left unchanged: backup-age-identity"
fi
echo "Encrypted systemd credentials installed. Secret values were not written to files or logs."
