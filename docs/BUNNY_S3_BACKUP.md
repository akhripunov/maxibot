# MAXIBOT Bunny S3 backup

This is an independent systemd scheduled backup, not part of the app release workflow. PostgreSQL and Docker perform the dump/restore; AWS CLI transfers objects to the confirmed private Bunny Storage Zone `maxibot-backups` in `de` (Frankfurt).

## Approved policy

- Daily backup, 30-day S3 retention, RPO 24 hours.
- Daily timer at 03:00 UTC (06:00 Moscow) with a five-minute scheduling jitter.
- Monthly isolated PostgreSQL and uploads restore drill.
- Monthly restore drill timer at 08:00 UTC on day 1, after the daily backup's four-hour timeout window; both jobs share a non-blocking lock.
- RTO target: 4 hours; each drill records elapsed seconds and the systemd unit has a four-hour timeout.
- Existing VPS recovery copies stay in place until a successful restore drill reads the object back from Bunny S3 and OWNER accepts the replacement policy.
- The system keeps the last successful restore-tested archive even after its 30-day age; expired other objects are eligible for deletion only after marker and object identity checks pass.

## Data and security

The daily job creates a PostgreSQL custom-format dump (`pg_dump -Fc`) and a globals/roles dump (`pg_dumpall --globals-only`), plus a separate archive of `maxibot-vps_maxibot_uploads`. PostgreSQL's dump snapshot is used for database consistency; head, extension list, and public table count are checked before and after the operation. A head/catalog change during the backup fails the run.

The payload is encrypted client-side with `age` before any upload. Bunny S3's documented API does not provide SSE/SSE-C; S3 credentials and the private age identity are supplied only through encrypted systemd credentials (`LoadCredentialEncrypted`). The age **public recipient** lives in `/etc/maxibot/bunny-backup.conf`; the corresponding private identity must also be kept by OWNER in an approved offline secret vault so recovery remains possible if this VPS is lost. Do not store the private identity in Git, Linear, logs, or chat.

The Bunny secret is read interactively by `provision-bunny-credentials.sh` and piped directly to `systemd-creds`; it is never a command argument or persistent plaintext file. AWS CLI reads the credential from the service's private credential directory. The S3 access key ID is the Storage Zone name; the Secret Access Key is entered from Bunny's Access settings.

After upload, the job checks object length and SHA-256 metadata, then downloads the full object from Bunny and compares its SHA-256 to the local encrypted archive. Temporary directories are mode 0700, files use umask 0077, and cleanup runs on success and failure. A restore uses a disk-backed temporary PostgreSQL data directory, a `--network none` container, and no production database or uploads volume. It restores roles, database, extensions, Alembic head, table count, and uploads in isolation. A failed restore cannot update the restore-tested marker or prune older S3 objects.

## VPS installation (not run by this code change)

Prerequisites on the approved VPS: systemd >= 250, Docker CLI/daemon, AWS CLI, `age`, `flock`, `python3`, `tar`, `sha256sum`, and PostgreSQL client utilities in the running PostgreSQL container. This change has not installed packages or started timers on production.

1. Generate a dedicated age keypair in a trusted environment. Store the private identity in OWNER's approved secret vault. Put only the public `age1…` recipient into `/etc/maxibot/bunny-backup.conf`.
2. Run `install-bunny-backup.sh` from the checked-out candidate on the VPS. It copies the script and systemd units, creates a configuration template if needed, reloads systemd, and deliberately does not enable timers.
3. Run `/usr/local/sbin/maxibot-provision-bunny-credentials` in the approved VPS console. Enter the Bunny Secret Access Key and the age private identity from the approved vault at the hidden prompts. Values are encrypted with the VPS systemd host key and are not written to shell history or logs.
4. Confirm the encrypted credential files are root-owned mode 0600, `/etc/maxibot/bunny-backup.conf` is root-owned and contains the public recipient, and `systemd-creds list` inside each service reports secure credentials.
5. Enable and start the daily and monthly timers. Run one daily backup and confirm the S3 object is private and its full GET readback hash passes.
6. Run one `maxibot-bunny-restore-drill.service` directly after that first upload. Keep every existing VPS recovery copy unless the drill passes, records its duration (must be <= 4 hours), and OWNER accepts the successful Bunny recovery evidence.

All systemd output is captured in journald and each job has a distinct identifier. No application monitoring notifier was found in tracked MAXIBOT code; before production enablement, confirm the existing VPS monitoring setup forwards failed systemd units/journal events or identify its already-approved alert unit. This change does not create a new notification service.

## Objects and retention

Daily archives are immutable-by-name under `maxibot/daily/<UTC timestamp>.tar.age`; the restore-tested pointer is the small non-secret JSON object `maxibot/control/last-restore-tested.json`. Bunny Storage does not support bucket versioning, so a missing/malformed pointer, missing tested object, checksum mismatch, truncated list, or S3 API error prevents deletion. Retention only deletes archive objects older than 30 days and never deletes the object referenced by the successful restore marker.

## Evidence status

The zone name, private-zone intent, S3 API enablement, region, and endpoint were confirmed by OWNER. The zone is currently empty. No Bunny credential was supplied to this session. Therefore no live S3 write/readback, real restore, timer activation, package installation, monitoring delivery, RPO/RTO operation, or deletion of old recovery copies is claimed PASS.
