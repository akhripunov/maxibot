# Release B0 — regional Telegram execution contract

Release B0 prepares, but does not activate, an EU Telegram executor. RU keeps
the ADS database and persistence authority; the executor has no PostgreSQL or
Redis credentials and supports only the typed read operations
`ACCOUNT_INVENTORY` and `CAMPAIGN_PERFORMANCE`.

The executor is reached only through HTTPS+mTLS and a versioned protocol. The
identity reference is opaque; sessions are encrypted in an executor-local
store. Owner assertion, account echo, protocol version and immutable release
SHA are mandatory. There is no arbitrary URL or Telegram write operation.

Production defaults remain `TELEGRAM_REMOTE_EXECUTION_ENABLED=false` and every
existing account remains `storage_region=RU`, `execution_region=RU`. An EU
account fails closed when the executor, identity, mTLS material or protocol is
not ready; it never falls back to RU implicitly.

Future activation is operator-only: provision and harden the EU host, install
the exact executor image, establish mTLS and an encrypted identity, run owner
and read-only smoke checks, then use the compare-and-set CLI for one account.
Rollback is an explicit EU→RU metadata change; storage remains RU. No EU DB or
tenant-data replication is part of B0.
