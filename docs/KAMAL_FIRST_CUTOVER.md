# MAXIBOT first Compose-to-Kamal cutover

This is a one-time production procedure. It is deliberately separate from the ordinary `tests → build → deploy` workflow. The regular workflow refuses a Compose baseline. Do not remove that guard until the one-time cutover has succeeded and the GitHub `production` Environment has been verified.

## Preconditions

Proceed only in a separately authorized release window after all of these are true:

- OWNER-managed production credential rotation is complete.
- The exact current `main` SHA passed the full CI suite and its GHCR image tag, digest, labels, `linux/amd64` platform, SBOM, and provenance were read back.
- The `production` GitHub Environment reviewers and self-review policy were inspected; the OWNER can authorize the job without weakening existing authorization.
- No active or uncertain MAXIBOT release is running. SSH host identity is verified.
- A fresh PostgreSQL backup passed `pg_restore --list` and an isolated restore with matching Alembic head, extensions, and tables. Keep the backup and the existing Compose API/Worker images available for recovery.
- The schema and frozen-path gates in the ordinary workflow pass. This procedure does not apply migrations or change the Compose file.
- The API has the current `kamal` and `maxibot-vps-frontend` networks available; DB and Redis can retain `maxibot-vps-internal` while receiving `kamal` aliases `db` and `redis`. Caddy stays on the frontend network.

If any precondition is missing or a command has an uncertain result, stop and reconcile read-only. Do not repeat a production mutation blindly.

## Cutover sequence

Run Kamal from the exact local checkout and pinned `Gemfile.kamal` for the approved SHA. Run Docker Compose and network commands on the VPS over SSH with strict host-key verification. Authenticate using the approved operator's existing SSH/GHCR credential flow; never copy credentials into this document or a command log.

1. Capture and retain the exact Compose API/Worker container IDs, image IDs/tags, release SHA, DB head, DB/Redis/Caddy container IDs, and all named volume identities. Confirm API polling is `false`, Compose Worker polling is `true`, and there is exactly one poller.
2. Confirm Docker network `kamal` exists (create it once only if absent). Connect the existing DB container to it with alias `db` and Redis with alias `redis`. Do not disconnect either from `maxibot-vps-internal`. Confirm Caddy remains on `maxibot-vps-frontend`.
3. Stop only the Compose Worker and verify no running container has `MAX_RUN_POLLING=true` before starting a Kamal Worker:

   ```sh
   ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$PRODUCTION_KNOWN_HOSTS_FILE" "root@$PRODUCTION_HOST" \
     'cd /opt/maxibot && docker compose --env-file /opt/maxibot/.env -p maxibot-vps -f /opt/maxibot/docker-compose.vps.yml stop worker'
   ```

4. Stop the Compose API. This can cause a brief public API interruption while Kamal starts; do not proceed unless the release window allows it:

   ```sh
   ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$PRODUCTION_KNOWN_HOSTS_FILE" "root@$PRODUCTION_HOST" \
     'cd /opt/maxibot && docker compose --env-file /opt/maxibot/.env -p maxibot-vps -f /opt/maxibot/docker-compose.vps.yml stop api'
   ```

5. With `MAXIBOT_COMMIT_SHA` set to the approved SHA and the run-scoped `IMAGE_VERSION` selected, run `bundle exec kamal config`, confirm the resolved hosts/roles/networks/volumes, then deploy API only:

   ```sh
   bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles api
   ```

6. Verify public `/health/live` and `/health/ready`; the ready response must report the approved SHA. From the new API container, open real TCP connections to `db:5432`, `redis:6379`, and `https:80`. Verify DB head, DB/Redis/Caddy IDs and volumes, uploads volume, and Caddy configuration still match the captured baseline.
7. Deploy the only Worker and verify its heartbeat, release SHA, `MAX_RUN_POLLING=true`, 512 MiB memory limit, and that it is the only poller:

   ```sh
   bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles worker
   ```

8. Verify both Compose API/Worker containers remain stopped, DB and Redis still use `maxibot-vps-internal`, Caddy still uses `maxibot-vps-frontend`, and the external health endpoint reports the approved SHA. Do not call the cutover complete until a GitHub Deployment record for the protected `production` Environment and the verification evidence exist.

Do not run Compose `up` as part of a successful cutover. Keep its stopped containers, pinned Compose file, and images until the Kamal release and recovery path are accepted.

## Failure and rollback

- Before stopping either Compose app container, an abort is read-only: leave production unchanged.
- If the API deploy or verification fails after Compose was stopped, keep the Compose Worker stopped. Inspect the active containers and poller count first. Stop any Kamal Worker and API with the pinned Kamal CLI; verify zero pollers before restoring the saved Compose API. Restore Compose API with the same strict-host-key SSH command and `docker compose ... up -d --no-build --no-deps api`. Verify the old API's SHA/health through Caddy before starting the Compose Worker with `docker compose ... up -d --no-build --no-deps worker`. Verify exactly one poller and the old Worker SHA afterward.
- Restore DB and Redis to their captured network membership only after all Kamal app containers are stopped. Never remove or recreate DB/Redis/Caddy containers or their volumes during recovery.
- After a fully successful Kamal release, ordinary application rollback is the stock `kamal rollback` operation to a recorded retained Kamal version. The first Compose baseline is not a Kamal release; returning to it uses the preceding Compose recovery procedure.
- If the previous containers, images, routes, or poller state do not match the recorded baseline, do not attempt recovery by guesswork. Stop and require read-only reconciliation and OWNER direction.

This document is not an authorization to run the cutover. Production mutation remains blocked until every precondition is freshly verified and OWNER authorization is explicit. Local `kamal` commands do not themselves create a GitHub Deployment; the cutover remains incomplete until its approved execution path records one in the protected Environment. Keep the current production release path until then.
