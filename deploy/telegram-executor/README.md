# Telegram executor (future EU node)

This is a separately deployable, read-only executor. It receives only typed
`ACCOUNT_INVENTORY` and `CAMPAIGN_PERFORMANCE` requests over mTLS, stores
encrypted identities locally, and never receives RU PostgreSQL/Redis/Yandex/MAX
credentials. The template is not started by the RU compose file.

Before activation, provision an approved EU host, production CA/certificates,
master key, and an immutable image built from the same release SHA as RU.
The image is published by the repository GitHub Actions workflow to the
private GHCR package `ghcr.io/akhripunov/maxibot-telegram-executor:<sha>`.
The future EU host needs only a minimum-scope read-only package-pull
credential (`read:packages`); it must not receive repository or package-write
permissions. Production compose must pin the SHA tag and preferably its
digest; it must never build from source on the EU host.
