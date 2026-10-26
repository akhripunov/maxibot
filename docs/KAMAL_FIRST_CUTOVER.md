# MAXIBOT first Compose → Kamal Proxy cutover

The target runs API and Worker under stock Kamal 2.12.0. Kamal Proxy is the only running public reverse proxy and owns ports 80/443. Compose PostgreSQL, Redis and their named volumes stay in place. Compose API, Worker, Caddy, Caddy configuration, image and recovery files remain available but stopped until OWNER confirms the migration; do not remove them during cutover or recovery.

The ordinary release workflow remains the three-job Tests → Build → Deploy path. It selects the prior successful immutable GHCR image and deploys API/Worker through Kamal without rebuilding. The one-time workflow `.github/workflows/maxibot-first-kamal-cutover.yml` is dispatch-only, uses the `production` Environment in both preflight and Apply, verifies the exact Build SHA/digest and only mutates production after the explicit Apply input and GitHub Environment approval. Preparation is not Apply authorization.

## Kamal Proxy and admin boundary

`config/deploy.yml` configures the API on port 8000 with `proxy.hosts` for `maxibot.pro`, `www.maxibot.pro`, `api.maxibot.pro`, `go.maxibot.pro` and `admin.maxibot.pro`, automatic TLS, HTTP→HTTPS redirect and `/health/ready`. Worker has `proxy: false`, `MAX_RUN_POLLING=true` and a 512 MiB memory limit. API has `MAX_RUN_POLLING=false`. API and Worker use Kamal's `kamal` network; DB and Redis join it with `db` and `redis` aliases while retaining `maxibot-vps-internal`.

The application serves the owner console only on the configured `WEB_ADMIN_RP_ID` host and sends its security headers from the application. A Host value only selects the console route; it is never an authentication factor. Browser POSTs require the exact configured HTTPS `WEB_ADMIN_ORIGIN`. WebAuthn verification still checks the exact RP ID and Origin; only the configured MAXIBOT owner can enroll or obtain a server-side owner session. The session cookie remains Secure, HttpOnly and SameSite=Strict. The session token and internal service credentials are never sent to browser JavaScript. Registration and login ceremonies are rate-limited in Redis and fail closed if Redis is unavailable. State-changing browser requests require the exact Origin; the QA machine-ingest route remains separately protected by its dedicated token.

The admin page keeps CSP (`frame-ancestors 'none'`, no objects, same-origin scripts/styles), HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store`. Bootstrap still requires the one-time owner bootstrap value and cannot replace an already enrolled Passkey. Login and registration keep user verification required. Do not add a token to the browser or trust forwarded headers for access control.

## Read-only preflight gates

Before Apply, the workflow must confirm:

1. The submitted SHA is the exact current-main lineage permitted by the workflow's allowlist, and its prior successful Build run, run attempt, GHCR digest, `linux/amd64`, source labels, SPDX SBOM and SLSA provenance match the immutable image. No build occurs in this workflow.
2. DNS resolves and the currently served TLS certificate verifies for all five public hostnames. The existing Compose Caddy container is running, its config validates, it alone owns ports 80 and 443, and no `kamal-proxy` container exists. The old Compose API's frontend `api` alias is unclaimed.
3. Compose API and Worker are running at the same SHA; API polling is false, Worker polling is true, there is exactly one Poller, and Worker memory is 536870912 bytes. PostgreSQL/Redis health, Alembic head, named volumes and API uploads volume are read back without changing them.
4. Fresh backup age/checksum and `pg_restore --list` pass. The existing disk-backed restore probe uses a unique temporary directory, no production DB volume, `--network none`, and checks tables, extensions and Alembic head. An EXIT trap removes its container and directory on success or failure.
5. Compose Caddy config/image/container and recovery data remain present. The preflight makes no network, container, port or application change. It does not require the currently known-broken Caddy→API network route to work before Apply; Apply attaches the saved Compose API to Caddy's existing frontend network and proves that recovery route before stopping Caddy.

If any gate fails, stop before Apply. Do not infer a PASS from old notes or prior production state.

## Authorized first cutover sequence

1. Re-read the same baseline, exact image and backup fingerprint immediately before mutation. Confirm the Caddy container is still the sole owner of both public ports.
2. Attach the existing Compose API to `maxibot-vps-frontend` with alias `api` only if no alias conflict exists. Keep it attached to `maxibot-vps-internal`. From Caddy, request `http://api:8000/health/live`; if this recovery check fails, stop with Caddy still serving traffic.
3. Create/reuse the `kamal` network and attach PostgreSQL and Redis with `db`/`redis` aliases while keeping both on `maxibot-vps-internal`. Verify the containers and volumes are unchanged.
4. Stop the Compose Worker, prove that no `MAX_RUN_POLLING=true` container remains, then stop the Compose API. Only then stop Caddy. Verify nothing owns 80/443 before Kamal Proxy starts; never run the two proxies on those ports together.
5. Run `bundle exec kamal deploy --skip-push --version "$IMAGE_VERSION" --roles api`. Kamal Proxy starts on 80/443 and provisions HTTPS for the five configured hosts. Verify `/health/ready` reports the exact candidate SHA on every HTTPS hostname; each HTTP URL redirects to HTTPS. Check the admin page security headers and owner-console shell.
6. Deploy the sole Worker with the same immutable image, then verify exact SHA/digest, API/Worker parity, API/Worker polling flags, one Poller across all running containers, Worker heartbeat and 512 MiB runtime/cgroup limit, DB/Redis health and identity, Alembic head, uploads/data volumes, Kamal Proxy ports, and that the old Compose API/Worker and Caddy are stopped.
7. Keep all recovery containers, Caddy files/images, backups and immutable image evidence. The VPS is not in its final state if any Caddy container is running. Retire/remove recovery material only after OWNER's final confirmation.

The transfer has a brief service interruption between stopping Caddy and Kamal Proxy becoming healthy. This is required to avoid simultaneous binding of 80/443; it is not a zero-downtime first cutover. Subsequent API releases use Kamal Proxy's normal health-checked version switch.

## Recovery to Compose and Caddy

If Apply or verification fails, do not retry Apply or roll back blindly. Reconcile running containers and Poller count read-only first. Keep PostgreSQL, Redis, all volumes, backup files, Caddy configuration/image/container, and Compose API/Worker containers. From the same pinned candidate and strict SSH configuration:

1. Stop Kamal Worker and prove there are zero Pollers; stop the Kamal API, then run `bundle exec kamal proxy stop` to free 80/443.
2. Start the existing stopped Compose API container. It was attached to both the internal and frontend networks with the `api` alias before Caddy was stopped. Verify its recorded pre-cutover SHA and local `/health/ready`.
3. Start the existing Caddy container; verify Caddy config, `http://api:8000/health/live`, public HTTPS and the old API SHA. Do not recreate Caddy or volumes.
4. Only after API/Caddy recovery is proven, start the existing Compose Worker and prove exactly one Poller, with the old Worker's expected SHA and polling setting.

If any step is uncertain, leave Worker stopped, preserve DB/Redis and all artifacts, and request read-only reconciliation. Stock Kamal `rollback` applies only after a successful Kamal release baseline exists; the first Compose version is recovered with the sequence above.

## Verification matrix

| Check | Local/static candidate | First cutover after Apply | Ordinary Kamal release |
|---|---|---|---|
| DNS + TLS: five hostnames | Workflow gate defined; not run from this local-only task | Required for each hostname | HTTPS requests required for each hostname |
| HTTPS health `/health/ready`, exact SHA | Workflow assertion defined | Required for each hostname | Required for each hostname |
| HTTP→HTTPS redirect | Workflow assertion defined | Required for each hostname | Required for each hostname |
| `admin.maxibot.pro` shell + CSP/security headers | App regression tests | Required | Required |
| Admin bootstrap then Passkey login/logout | Unit/contract and browser-flow source checks; real owner Passkey NOT run locally | Owner manual functional confirmation plus session/privacy check required | Existing enrollment must remain usable |
| Forged Host/Origin/header, session owner check, Redis rate limit | Focused security tests | Included in exact candidate before Apply | Included in ordinary candidate CI |
| API readiness and one Worker/Poller | Existing runtime scripts and workflow assertions | Exact SHA/digest, one Poller, heartbeat, 512 MiB required | Same runtime assertions |
| PostgreSQL, Redis, Alembic and volumes | Read-only/isolated restore gates defined | IDs, head and volumes unchanged | Same IDs, head and volumes |
| Proxy ports / no Caddy | Static workflow assertions | Kamal Proxy owns 80/443; no running Caddy | Same |

This document does not authorize production Apply. No production action is reported by a local test result.
