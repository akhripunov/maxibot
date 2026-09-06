# BOT and CHANNEL Metrika processors

`BOT_AND_CHANNEL_CONVERSION_PROCESSORS_MUST_BE_OPERATIONALLY_SEPARATE=YES`.

The promoted-BOT path uses `BotConversion` / `bot_conversion_queue` and
`app.traffic.system_conversion_processor`. Its selection must filter for
non-empty yclid and ClientID before applying a batch limit. Historical BOT
rows without yclid may be marked permanently ineligible; missing ClientID
remains retryable because the browser can bind it later. BOT delivery state is
durable and does not change BOT attribution or goal semantics.

The CHANNEL path uses `OfflineConversion` / `offline_conversion_queue` and
the exact-membership sender in `app/bot/menu.py`, followed by
`app.traffic.metrica_upload_tracker`. CHANNEL selection, attribution,
dedupe, user journey and goals are independent and must not be routed through
the BOT processor.

Shared low-level Metrika client code is allowed. A shared pending-selection
loop or shared backlog is forbidden.
