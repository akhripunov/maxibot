# Automatic landing creation flow

## Implemented

Opening `promotion_landing:<channel_id>` now calls the existing
`MenuRepository.create_landing()` before rendering. That method remains the
single idempotent creation path: it returns the active landing when one
already exists and preserves the existing `TrafficProfile.target_url`.

The call is made only for an owned channel because `create_landing()` verifies
`Channel.admin_id` before creating anything. An unavailable/foreign channel
therefore cannot create a landing.

The lookup key is `LandingPage.channel_id`, so Channel A and Channel B cannot
share an active landing: each receives a different opaque `public_id` and URL.
Repeated opens for the same channel return the existing active row. Each
landing request creates its own `TrafficVisit` row linked to that landing and
channel, carrying its own UTM/yclid values; visits do not create additional
landing pages.

The landing screen no longer exposes a separate “Создать лендинг” button. It
renders the generated public URL immediately and shows a warning when the
closed MAX target URL is not configured. The target URL remains separately
editable in promotion settings.

The URL is built by `public_landing_url()` from `TRAFFIC_PUBLIC_BASE_URL` and
has exactly one slash before the opaque `public_id`. The production default is
`https://go.maxibot.jobjin.ru`, so an example is
`https://go.maxibot.jobjin.ru/77459027312767`. Existing environments can override the
domain through `TRAFFIC_PUBLIC_BASE_URL`; `MAX_LANDING_BASE_URL` remains
documented as the compatible landing-domain setting.

The URL is accepted by `GET /{public_id}` (and the legacy `/go/{public_id}`
route). That endpoint records UTM/yclid visit data and continues through the
existing direct redirect to `TrafficProfile.target_url`; the target URL is not
changed by landing creation.

## Safety

No changes were made to the attribution engine, auto-approve worker, WebSocket
transport, database schema, migrations, or production flags. Existing landing
redirect code was not changed.
