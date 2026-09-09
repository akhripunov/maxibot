# Canonical production release pipeline

The only production release entrypoint is:

```text
scripts/release_prod.sh <FULL_SHA> --dry-run
scripts/release_prod.sh <FULL_SHA> --apply
```

`--apply` requires `OWNER_APPROVED=1`, `PRODUCTION_HOST`, and
`PRODUCTION_SSH_KEY`. The command is fail-closed and must run from the
repository containing the exact commit.

If the repository has multiple Alembic heads, set `TARGET_MIGRATION` to the
intended revision. The entrypoint never guesses an Alembic branch.

The source SHA is proven at the Git boundary and copied with `git archive`.
The VPS release directory is not required to be a Git checkout. Each release
uses `/opt/maxibot/releases/<full-sha>/`, a `release-manifest.json`, and the
image label `org.maxibot.release.sha`.

The only production secret source is `/opt/maxibot/.env`. Release archives do
not contain secret copies, and generated Compose uses the absolute
`env_file: /opt/maxibot/.env`. No release symlinks are created.

Images are built and checked as `linux/amd64`; `x86_64` is treated as the
equivalent host spelling of `amd64`. The gate resolves Compose, captures the
previous runtime SHA/image/Alembic head, validates rollback metadata, and
checks the target migration before allowing `alembic upgrade head`.

The runtime switch occurs only after `MIGRATION_ALLOWED=YES`. The pipeline
then checks API ready/live, exact SHA, Alembic head, image parity, and polling
roles. It writes `release-report.env` and a rollback Compose snapshot into the
immutable release directory. Runtime rollback can restore the previous Compose
release. Database rollback is never automatic: data-loss safety must be proven
separately.

Dry-run does not build in production, migrate the database, switch containers,
or mutate production data. Migration before release, runtime-switch, and
rollback readiness is forbidden.
