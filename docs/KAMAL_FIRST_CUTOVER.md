# MAXIBOT first Compose-to-Kamal cutover

This is a one-time production procedure. It is deliberately separate from the ordinary `tests → build → deploy` workflow. The regular workflow refuses a Compose baseline. Do not remove that guard until the one-time cutover has succeeded and the GitHub `production` Environment has been verified.

## Preconditions

Proceed only in a separately authorized release window after all of these are true:

- OWNER-managed production credential rotation is complete.
- The exact current `main` SHA passed the full CI suite. Its run-scoped GHCR tag, immutable digest, and `linux/amd64` platform were read back by the hosted Build job. The Build logs confirm SBOM and provenance generation. Before Apply, an approved runner must pull the digest and verify all three OCI source-SHA labels and the published SBOM/provenance attestations.
- The `production` GitHub Environment reviewers and self-review policy were inspected; the OWNER can authorize the job without weakening existing authorization.
- No active or uncertain MAXIBOT release is running. SSH host identity is verified.
- A fresh PostgreSQL backup passed `pg_restore --list` and an isolated restore with matching Alembic head, extensions, and tables. Keep the backup and the existing Compose API/Worker images available for recovery.
- The schema and frozen-path gates in the ordinary workflow pass. This procedure does not apply migrations or change the Compose file.
- The API has the current `kamal` and `maxibot-vps-frontend` networks available; DB and Redis can retain `maxibot-vps-internal` while receiving `kamal` aliases `db` and `redis`. Caddy stays on the frontend network.

If any precondition is missing or a command has an uncertain result, stop and reconcile read-only. Do not repeat a production mutation blindly.

## Latest read-only state and unresolved incident gates

This snapshot is evidence for preparation only, not an Apply baseline. It must be re-read through the authorized production path immediately before any cutover.

- Published candidate: `4f01e8751a5edefede2018612cef07cde676f3a3`; successful hosted Tests/Build run `40213945382`; GHCR tag `ghcr.io/akhripunov/maxibot-vps-api:4f01e8751a5edefede2018612cef07cde676f3a3-40213945382-1`; digest `sha256:7b8aa65628507b61269d59e2d427f32540f406ae1a22d8c733b0c41d52257c97`.
- The hosted Build verified the registry tag/digest and `linux/amd64`. Its log shows all three source-SHA labels set to the published SHA, SBOM generation, provenance resolution, and exported BuildKit attestation manifest `sha256:a46fcb2de1e662297a0274d9650b843b611aec9ae1d6ce5e569a89fca2a669f2`. Registry-side config-label and attestation readback by the Deploy job remains pending; do not treat the build inputs alone as that readback.
- Read-only VPS inventory after failed legacy run `40183629031`: API and Worker are still Compose containers on image digest `sha256:58c38e09f5198393d38c154275f7e9e70a4323fce15083bc464c416e8a6d1dae`, both labelled `d6ba66416645ffb85f619a2b3e204fca2bb789a5`. API is running/healthy with `MAX_RUN_POLLING=false`; Worker is running with `MAX_RUN_POLLING=true`; both have a 536870912-byte memory limit. One poller is present.
- PostgreSQL and Redis are running; PostgreSQL was healthy. The read-only DB query reported Alembic head `0098_durable_autopilot_execution_claim_v1`. Caddy is running, its Caddyfile validation passed, and its configured upstream is `api:8000`.
- The observed API and Worker are attached only to `maxibot-vps-internal`; Caddy is attached only to `maxibot-vps-frontend`. The `kamal` network does not exist. Since the API has no shared network with Caddy, live Caddy-to-API reachability is NOT_VERIFIED and the Compose topology does not match `docker-compose.vps.yml`. Reconcile this discrepancy before cutover; do not change network membership during preparation.
- Disk had 5.2 GB free (87% used). The latest listed dump was `maxibot-db-me53-9800871-20261025T163501Z.dump` (70,701,583 bytes, timestamp 2026-10-25 16:36:27 UTC). Its `pg_restore --list` and isolated restore have NOT been verified. The older 2026-10-24 isolated-restore evidence does not verify this new dump.
- Legacy run `40183629031` failed with `worker_memory_limit_mismatch`; its rollback failed because `/opt/maxibot/backups/.env` was missing. Do not rely on that rollback path. The recovery precondition is to verify, without printing values, that the active Compose env files and previous API/Worker images required by the documented recovery commands are present and usable. If not, stop before stopping either Compose app.
- Runtime credential rotation remains unconfirmed. The unexpected environment-output incident must be closed through the owner-managed rotation process before any production Apply. File timestamps or mere file presence are not proof of rotation.

The Kamal migration made no production changes. The legacy run above did fail during Apply and rollback, so treat this live snapshot as a recovered Compose state only after the owner-approved reconciliation confirms it. The first-cutover procedure is **NOT READY** until the current topology discrepancy, GHCR image metadata/attestation readback, credential rotation, fresh-backup integrity/isolated restore, live Compose recovery prerequisites, GitHub Environment authorization, and current health/routing evidence are resolved.

Per OWNER sequence, keep the ME-53 white-screen code and policy commits out of this infrastructure cutover. Include that fix in the separate next Kamal candidate after this first cutover is verified.

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
- Do not invoke the legacy automatic rollback used by run `40183629031`; that rollback failed while looking for `/opt/maxibot/backups/.env`. Use only the explicit recovery sequence above after verifying the actual Compose env-file paths and retained images. A missing recovery env file or mismatched baseline blocks any stop/cutover action.
- Restore DB and Redis to their captured network membership only after all Kamal app containers are stopped. Never remove or recreate DB/Redis/Caddy containers or their volumes during recovery.
- After a fully successful Kamal release, ordinary application rollback is the stock `kamal rollback` operation to a recorded retained Kamal version. The first Compose baseline is not a Kamal release; returning to it uses the preceding Compose recovery procedure.
- If the previous containers, images, routes, or poller state do not match the recorded baseline, do not attempt recovery by guesswork. Stop and require read-only reconciliation and OWNER direction.

This document is not an authorization to run the cutover. Production mutation remains blocked until every precondition is freshly verified and OWNER authorization is explicit. Local `kamal` commands do not themselves create a GitHub Deployment; the cutover remains incomplete until its approved execution path records one in the protected Environment. Keep the current production release path until then.
