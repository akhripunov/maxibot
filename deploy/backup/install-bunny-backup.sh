#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" != 0 ]]; then
  echo "Run this installer on the VPS as root through its approved access path." >&2
  exit 1
fi
systemd_version="$(systemctl --version | awk 'NR == 1 {print $2}')"
[[ "$systemd_version" =~ ^[0-9]+$ && "$systemd_version" -ge 250 ]] || {
  echo "systemd 250 or newer is required for encrypted service credentials." >&2
  exit 1
}
for command in install systemctl systemd-creds docker aws age flock python3 tar sha256sum awk df date wc tr sed grep cmp mktemp rm chmod mkdir cp ln; do
  command -v "$command" >/dev/null 2>&1 || { echo "Required command unavailable: $command" >&2; exit 1; }
done
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
install -d -o root -g root -m 0755 /usr/local/sbin /etc/maxibot /etc/systemd/system
install -o root -g root -m 0755 "$script_dir/maxibot-bunny-backup.sh" /usr/local/sbin/maxibot-bunny-backup
install -o root -g root -m 0755 "$script_dir/provision-bunny-credentials.sh" /usr/local/sbin/maxibot-provision-bunny-credentials
install -o root -g root -m 0644 "$script_dir/maxibot-bunny-backup.service" /etc/systemd/system/
install -o root -g root -m 0644 "$script_dir/maxibot-bunny-backup.timer" /etc/systemd/system/
install -o root -g root -m 0644 "$script_dir/maxibot-bunny-restore-drill.service" /etc/systemd/system/
install -o root -g root -m 0644 "$script_dir/maxibot-bunny-restore-drill.timer" /etc/systemd/system/
if [[ ! -e /etc/maxibot/bunny-backup.conf ]]; then
  install -o root -g root -m 0600 "$script_dir/bunny-backup.conf.example" /etc/maxibot/bunny-backup.conf
  echo "Created config template; set BUNNY_AGE_RECIPIENT before enabling timers." >&2
fi
systemctl daemon-reload
echo "Backup units installed but not enabled or started. Provision credentials and verify the public age recipient first."
