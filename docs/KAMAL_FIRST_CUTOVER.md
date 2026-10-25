# MAXIBOT first Compose-to-Kamal cutover

This is a one-time production procedure. It is deliberately separate from the ordinary `tests → build → deploy` workflow. The regular workflow refuses a Compose baseline. Do not remove that guard until the one-time cutover has succeeded and the GitHub `production` Environment has been verified.

## GitHub-hosted one-time execution path

The dedicated workflow is `.github/workflows/maxibot-first-kamal-cutover.yml`. It is `workflow_dispatch` only, restricted to `main` and the repository OWNER. The workflow requires current `main` to remain at its dispatch SHA, while the selected image SHA must be the exact successful `maxibot-native-release.yml` Build SHA and an ancestor of that `main`. To reuse the already-built `4f01e875…` image safely, GitHub Compare must show that the only paths changed since that Build are `.github/workflows/maxibot-first-kamal-cutover.yml`, `docs/KAMAL_FIRST_CUTOVER.md`, and `tests/release/test_first_kamal_cutover_workflow.py`. It enforces that exact allowlist during read-only preflight and repeats the comparison after Apply approval immediately before the first production mutation; Dockerfile, runtime, dependency, Kamal, migration, or any other path fails closed. It verifies the Build run/attempt and existing run-scoped GHCR tag against the submitted digest, pulls by digest, checks `linux/amd64` and all three source-SHA labels, and requires the BuildKit SBOM/provenance descriptors in that digest's OCI index. It does not build or upload an image. Environment readback uses the supported `GITHUB_TOKEN` permission `actions: read`.

`confirm_apply=PREFLIGHT_ONLY` runs only the protected preflight. `APPLY_FIRST_COMPOSE_TO_KAMAL` makes the separate `apply` job eligible only after preflight passes; the `apply` job waits again on the `production` Environment. Both jobs read back that the Environment has exactly the repository OWNER as required reviewer and that self-review is allowed for this explicit OWNER approval path. They reject active Native/Kamal release runs. The workflow fails before SSH unless the OWNER attests `ROTATED` and supplies a non-secret `ME-51:` evidence reference. This input is an attestation; it does not itself rotate or inspect secret values.

The preflight checks the live Compose API/Worker/DB/Redis/Caddy baseline, API connectivity to `db`, `redis`, and `https`, Caddy's request to `api:8000`, one running poller, 512 MiB Worker limit, Compose recovery env files/images, current backup age/catalog, and an offline isolated PostgreSQL restore matching Alembic head, extensions, and table count. The restore probe is a temporary `--network none` container on the VPS and is removed before preflight completes; it does not mount or connect to production data volumes. A passing preflight is read-only for running services and persistent production data.

Apply rereads the baseline and backup checksum before mutation. Only after the separate Apply input and Environment approval does it connect DB/Redis to `kamal` while keeping `maxibot-vps-internal`, stop Compose Worker and prove zero pollers, stop Compose API, deploy API then the sole Worker with stock Kamal, and verify exact digest/SHA, both API networks, Caddy reachability, unchanged DB/Redis/Caddy IDs and volumes, database head, polling flags, Worker heartbeat and 512 MiB runtime limit. Failure prints the documented manual recovery sequence; it never runs an automatic Compose/Kamal rollback.

GitHub may show the read-only `preflight` job in the `production` Environment deployment history because Environment is where the existing SSH secrets and approval gate are scoped. Distinguish it from the `apply` job using the job name and run summary; only `apply` performs the cutover.

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
- Disk had 5.2 GB free (87% used). The latest listed dump was `maxibot-db-me53-9800871-20261025T163501Z.dump` (70,701,583 bytes, timestamp 2026-10-25 16:36:27 UTC). The active Release Queue later recorded `pg_restore --list` and isolated restore PASS for this dump, SHA-256 `dca25c3a06be36cdb1c4a387cf34073837d7dd058d8ade04d801f966e71de290`, 63 restored tables, and Alembic head `0098_durable_autopilot_execution_claim_v1`. I verified the Queue record, not its underlying restore log. Reconfirm a fresh backup and isolated restore in the approved cutover execution before stopping either app container.
- Legacy run `40183629031` failed with `worker_memory_limit_mismatch`; its rollback failed because `/opt/maxibot/backups/.env` was missing. Do not rely on that rollback path. The recovery precondition is to verify, without printing values, that the active Compose env files and previous API/Worker images required by the documented recovery commands are present and usable. If not, stop before stopping either Compose app.
- Runtime credential rotation remains unconfirmed. The unexpected environment-output incident must be closed through the owner-managed rotation process before any production Apply. File timestamps or mere file presence are not proof of rotation.

The Kamal migration made no production changes. The legacy run above did fail during Apply and rollback, so treat this live snapshot as a recovered Compose state only after the owner-approved reconciliation confirms it. The first-cutover procedure is **NOT READY** until the current topology discrepancy, GHCR image metadata/attestation readback, credential rotation, fresh-backup integrity/isolated restore, live Compose recovery prerequisites, GitHub Environment authorization, and current health/routing evidence are resolved.

## Access check on 2026-10-25

- GitHub API readback confirms `main=4f01e8751a5edefede2018612cef07cde676f3a3`; hosted run `40213945382` is SUCCESS, and its `tests`/`build` jobs succeeded while `deploy` was skipped. The active-runs query returned zero.
- Hosted Build logs record the run-scoped tag, exact digest/platform verification, source-SHA labels, SBOM generation, provenance resolution, and attestation manifest `sha256:a46fcb2de1e662297a0274d9650b843b611aec9ae1d6ce5e569a89fca2a669f2`. The approved runner's later digest pull and OCI label checks remain part of protected Deploy preflight.
- The GitHub integration cannot read `production` Environment settings; its environment API request was rejected. On 2026-10-25 the host Mac `gh api user` check ended in a TLS handshake timeout. `gh auth status` output under this constrained shell is not sufficient to conclude that host Keychain authorization expired; account authorization and current Environment reviewer/self-review settings remain NOT_VERIFIED until the existing host Mac CLI path can reach GitHub again. Do not create a replacement token.
- The existing VPS SSH key is readable locally and the VPS host key is pinned. A read-only SSH attempt with `BatchMode=yes` and `StrictHostKeyChecking=yes` failed before authentication with `Operation not permitted` when opening `135.106.166.46:22`. No sandbox/network policy or SSH setting was changed. Direct production reconciliation is blocked by the current execution environment's network policy.
- Runtime credential rotation remains unconfirmed. No production state was read or changed in this access check.

To restore the allowed path, OWNER must request an environment-policy allowlist for read-only SSH to `135.106.166.46:22` using the existing key and pinned host key, or make the existing protected GitHub Environment path available for the one-time cutover. Do not replace this with a relaxed host-key check. OWNER must also confirm rotation of credentials exposed from `/opt/maxibot/.env` and `/opt/maxibot/secrets/mail_cloud.env` through the established secret-management procedure, recording only affected variable names and completion in ME-51.

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
- If the API deploy or verification fails after Compose was stopped, keep the Compose Worker stopped. Inspect active containers and poller count first. From the same pinned checkout and strict SSH configuration, stop Kamal roles in this order: `bundle exec kamal app stop --roles worker`, then `bundle exec kamal app stop --roles api`. Verify zero `MAX_RUN_POLLING=true` containers before restoring the saved Compose API with the strict-host-key SSH command and `docker compose ... up -d --no-build --no-deps api`. Verify the recorded old API SHA and health through Caddy before starting the Compose Worker with `docker compose ... up -d --no-build --no-deps worker`. Verify exactly one poller and the recorded old Worker SHA afterward.
- Do not invoke the legacy automatic rollback used by run `40183629031`; that rollback failed while looking for `/opt/maxibot/backups/.env`. Use only the explicit recovery sequence above after verifying the actual Compose env-file paths and retained images. A missing recovery env file or mismatched baseline blocks any stop/cutover action.
- Restore DB and Redis to their captured network membership only after all Kamal app containers are stopped. Never remove or recreate DB/Redis/Caddy containers or their volumes during recovery.
- After a fully successful Kamal release, ordinary application rollback is the stock `kamal rollback` operation to a recorded retained Kamal version. The first Compose baseline is not a Kamal release; returning to it uses the preceding Compose recovery procedure.
- If the previous containers, images, routes, or poller state do not match the recorded baseline, do not attempt recovery by guesswork. Stop and require read-only reconciliation and OWNER direction.

This document is not an authorization to run the cutover. Production mutation remains blocked until every precondition is freshly verified and OWNER authorization is explicit. Local `kamal` commands do not themselves create a GitHub Deployment; the cutover remains incomplete until its approved execution path records one in the protected Environment. Keep the current production release path until then.
