# MAXIBOT ADS data model (canonical V1)

The ADS layer is provider-neutral at its ownership boundary and provider-specific
at collection time. `ads_accounts` stores connected provider accounts;
`ads_campaigns` stores the provider campaign identity and metadata. The canonical
ownership relation is `ads_promotion_campaign_links`: one row links exactly one
`CHANNEL` (`channel_id`) or one `BOT` (`bot_promotion_profile_id`) to one internal
campaign (`campaign_id`), with `ACTIVE`/`INACTIVE` status and database-enforced
foreign keys. A link is the collection scope; campaign names are never a scope
heuristic.

`ads_channel_campaign_links` is retained as a compatibility table. Migration
0074 copies its rows and statuses into the generic table without activating an
inactive row. New channel links are written to the generic table. The current
system BOT identity is the `bot_promotion_profiles` row `bot_key=maxibot`; its
exact Yandex Direct campaign identity is external id `713929699` under the
connected `YANDEX_DIRECT` account.

Collection selects distinct campaigns with an active generic link, a connected
account, and a non-archived campaign. The existing cadence is unchanged:
campaign 60 minutes, granular 360 minutes, entity configuration 1440 minutes;
search-query collection remains disabled. The canary setting is optional
diagnostic configuration and is not required for business collection.

Existing normalized storage remains authoritative: `ads_v2_entities` stores
provider entities; `ads_v2_report_runs` stores report provenance; and
`ads_v2_observations` plus `ads_v2_daily_aggregates` store dimensional and daily
metrics. `ads_snapshots` stores bounded campaign facts and may be associated
with a BOT-scoped campaign without a channel. Raw provider payloads remain
bounded metadata, not a creative archive. Search-query collection, creative
asset retention, retention policy, AI reasoning, BOT/CHANNEL runtime semantics,
and Direct writes are unchanged.

AI selection remains channel-compatible through the generic link resolver and
uses the same fact/dimension dataset and reasoning policy. Future providers
(for example VK) can attach to the same account/campaign/link model without
changing the object domains or collection semantics.
