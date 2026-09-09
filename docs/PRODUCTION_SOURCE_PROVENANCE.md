# Recovered production source baseline

This source snapshot was recovered read-only from the currently deployed
production image because the original runtime Git commit object was not
available from the canonical GitHub remote.

- `SOURCE_PROVENANCE`: `DEPLOYED_PRODUCTION_IMAGE`
- `DEPLOYED_RUNTIME_SHA`: `9b3a0f6ce14d8503cf581a50b954d467caeb53a5`
- `IMAGE_DIGEST`: `sha256:f5a45837a27e28164cfdb5c262d6e5a82e9e0431f28a6aa5ecd1c7280fb4c4aa`
- `DB_HEAD`: `0081_ads_bot_ai_support_v1`

The Git commit containing this file is a recovery bootstrap source baseline.
It is not the missing original runtime Git commit and must not be reported as
the deployed runtime SHA.
