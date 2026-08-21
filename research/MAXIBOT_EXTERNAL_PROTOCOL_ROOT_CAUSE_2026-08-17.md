# MAXIBOT external-protocol blocker: independent root-cause analysis

Date: 2026-08-17

Scope: read-only analysis. Production code, configuration, DB, worker, MAX and
Yandex state were not changed.

## Required conclusion before any production change

```text
ROOT_CAUSE = PROVEN FOR THE TESTED DESKTOP WEB FUNNEL:
1. macOS has no registered application handler for the `max:` URL scheme, so
   the landing's native-only handoff cannot advance on this Mac.
2. A fresh official `https://web.max.ru/se13802047_1_bot?startapp=...` route
   does open the MAXIBOT Mini App and preserves signed `start_param`.
3. After identity/channel-open, MAXIBOT used `window.location.replace()` from
   inside the Mini App iframe. This navigated only that iframe to the channel
   invite and did not open the channel in the containing MAX client.

EVIDENCE =
1. Same Mac, same Chrome and same external scheme: mx-t.su opens MAX while
   go.maxibot.jobjin.ru does not.
2. The public profiles of competitor and MAXIBOT expose the same startapp link
   forms. The `id`/`se` prefix describes owner type; it is not evidence of a
   functional difference between bots.
3. Removing startapp, changing token length, removing JS/timers/iframes and
   using a plain anchor on go.maxibot did not make the handler launch.
4. A fresh MAXIBOT Web startapp route launches its configured Mini App and
   passes the same opaque token in signed MAX WebApp data.
5. Chromium implements the decision as an origin+protocol pair in
   prefs::kProtocolHandlerPerOriginAllowedProtocols. UNKNOWN prompts; an
   allowed pair launches without the security prompt. The initiating origin is
   explicitly passed to ExternalProtocolHandler::GetBlockState().

WHY_MX_T_WORKS IN THE OBSERVED DESKTOP WEB PATH =
Its native `max:` attempt cannot launch on this Mac because LaunchServices has
no handler. However, its explicit `https://web.max.ru/id..._bot?startapp=...`
fallback is accepted by MAX Web. After that fallback is clicked, MAX Web
automatically creates a Mini App iframe for `app_bot_check.php`, preserving the
same opaque value as `WebAppStartParam`/signed `start_param`. The intermediate
Mini App is brief and the existing target channel becomes visible.

WHY_MAXIBOT_FAILS =
Chrome emits the external navigation request, but macOS cannot dispatch it.
The OS returns `NSOSStatusErrorDomain -10814 / kLSApplicationNotFoundErr`:
no application claims `max://`. Repeated landing/deeplink changes cannot
create an operating-system protocol handler.

The earlier desktop web fallback trial used a state that did not demonstrate a
fresh startapp launch and led to an incorrect `id` versus `se` inference. A
new visit with a fresh one-time token disproved that inference: MAX Web opened
the MAXIBOT Mini App automatically and supplied signed identity/start_param.
The remaining redirect failure was inside the Mini App iframe.

PROPOSED_FIX =
- desktop landing: automatically use the official MAX Web bot startapp route;
- mobile landing: use the official HTTPS MAX bot startapp route;
- after verified identify/channel-open: call
  `window.WebApp.openMaxLink(target_url)` from the running Mini App, with the
  old location redirect only as a non-Bridge fallback.

For public first-time desktop Chrome users, zero-click external-protocol launch
cannot be guaranteed by landing HTML. The product-safe choices are:
- keep automatic native attempt, then show a single explicit Open MAX CTA for
  the first origin permission; or
- use a platform-supported HTTPS/App-Link route if MAX provides one that opens
  the Mini App directly while preserving startapp.
Direct channel fallback is not acceptable for EXACT attribution.

ATTRIBUTION_IMPACT = NONE.
No change is proposed to visit, yclid, one-time startapp, verified Mini App
identity, MaxIdentityLink, user_added, EXACT or conversion queue.
```

## Chromium evidence

Primary source:

- https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/external_protocol/external_protocol_handler.cc

Relevant behavior in current Chromium source:

- `GetBlockState()` receives the initiating origin and scheme.
- It checks enterprise `AutoLaunchProtocolsFromOrigins` policy.
- It then checks `prefs::kProtocolHandlerPerOriginAllowedProtocols` using the
  serialized initiating origin and scheme.
- A stored allow returns `DONT_BLOCK`; otherwise the state is `UNKNOWN` and
  Chrome uses its external-protocol confirmation path.
- External-protocol anti-flood state is reset by user gesture, bookmark or
  typed navigation; an automatic page-load request is not a user gesture.

The Chromium unit tests independently verify that allow decisions for
`{scheme 1, origin 1}` and `{scheme 2, origin 2}` are stored and resolved
independently:

- https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/external_protocol/external_protocol_handler_unittest.cc

## Status

```text
DOWNSTREAM_MINIAPP_ATTRIBUTION = PREVIOUSLY_PROVEN
DEEPLINK_SYNTAX_DIFFERENCE = EXCLUDED AS ROOT CAUSE
BOT_PREFIX_CAUSALITY = DISPROVEN / NOT A FUNCTIONAL DIFFERENCE
MACOS_MAX_APP_PRESENT = NO
MACOS_MAX_SCHEME_HANDLER = NO
SYSTEM_MAX_URL_OPEN = kLSApplicationNotFoundErr
ROOT_CAUSE_PROVEN = YES
PRODUCTION_CHANGED = NO (controlled fallback trial was rolled back)
```

Chrome profile preferences, cookies and storage were not read. The decisive
proof came from macOS LaunchServices and a direct system open of the native
scheme.

## Competitor-parity web fallback deploy

After the OS blocker was proven, both live landings were compared in the same
Chrome session. `mx-t.su` exposes both a native bot startapp link and a
`web.max.ru` bot startapp link with the same token. MAXIBOT exposed only the
native link.

MAXIBOT was updated to expose both bot routes with the same one-time token.
The browser fallback is not a direct channel invite and does not bypass the
Mini App identity contract.

Controlled trial image: `maxibot-vps-api:20260817-webfallback`.

```text
API_HEALTH = READY
API_MAX_RUN_POLLING = false
WORKER_MAX_RUN_POLLING = true
NATIVE_STARTAPP_LINK = YES
WEB_STARTAPP_LINK = YES
SAME_SERVER_VISIT_TOKEN_SOURCE = YES
DIRECT_CHANNEL_BYPASS = NO
TARGETED_TESTS = 21 PASS, 1 environment-incompatible test deselected
```

Real Chrome result on the tested Mac:

```text
NATIVE_MAX_OPEN = NO (no macOS max: handler)
WEB_MAX_OPEN = YES
WEB_MAX_DESTINATION = authenticated MAX Web home
WEB_MINI_APP_OPEN = NO
WEB_START_PARAM_CONSUMED = NOT_OBSERVED
CHANNEL_OPEN = NO
FULL_FUNNEL = BLOCKED until native MAX is installed or MAX Web supports this
              bot startapp route in the active client/session
```

An additional controlled numeric bot-id test opened the MAXIBOT chat, but MAX
Web did not preserve the startapp identity into the Mini App. The backend
received `POST /api/traffic/identify` with HTTP 422. Thus neither the `se...`
web route nor numeric bot-id substitution satisfies the attribution contract.

Because the controlled fallback failed acceptance, it was rolled back.
Production returned to `maxibot-vps-api:20260816-nativefinal`; API health is
ready, API polling is false, and the existing worker remains running with
polling true.

The legacy VPS check could not be repeated during this deploy because the
available SSH identities were rejected. The production host showed one
running worker and the API remained `MAX_RUN_POLLING=false`.

## Fresh competitor runtime trace

The competitor landing was opened in the same Chrome profile and left without
clicks. It remained on the branded landing; the automatic native attempt did
not open MAX. Therefore zero-click desktop behavior is not reproduced on this
Mac.

After one explicit click on `Открыть в браузере`, the following sanitized
runtime chain was observed:

```text
mx-t.su landing
→ web.max.ru/id..._bot?startapp=<opaque>
→ MAX Web target surface
→ iframe max-click.tgtrack.ru/app_bot_check.php
→ WebAppStartParam=<same opaque>
→ signed WebAppData contains user/chat/hash/start_param keys
```

No raw token, signed payload, MAX user id, IP, cookies or personal data are
included in this report.

```text
COMPETITOR_ZERO_CLICK_ON_THIS_MAC = NO
COMPETITOR_BROWSER_FALLBACK_CLICK = 1
COMPETITOR_WEB_MINIAPP = YES
COMPETITOR_START_PARAM_PRESERVED = YES
MAXIBOT_WEB_MINIAPP = YES WITH FRESH TOKEN
MAXIBOT_WEB_START_PARAM = YES WITH FRESH TOKEN
PLATFORM_ROUTE_DIFFERENCE_ID_VS_SE = NOT PROVEN; PREVIOUS CLAIM RETRACTED
MAXIBOT_CHANNEL_URL_LOADED_INSIDE_IFRAME = YES
MAXIBOT_TOP_LEVEL_CHANNEL_OPEN = NO BEFORE BRIDGE FIX
```

## Corrected production fix and E2E

The `id..._bot` versus `se..._bot` hypothesis was rejected. Both public bot
profiles expose the same functional launch contracts. A fresh MAXIBOT visit
proved that MAX Web consumes the `se..._bot?startapp=...` route and launches
the configured Mini App with signed identity/start_param.

The exact second-half blocker was then reproduced: `traffic.html` called
`window.location.replace(target_url)` from inside the MAX Web Mini App iframe.
The iframe itself navigated to `https://max.ru/join/...`, while the containing
MAX Web screen stayed unchanged.

Production image: `maxibot-vps-api:20260817-openmaxlink`.

Changes:

- desktop landing automatically redirects to
  `https://web.max.ru/<bot>?startapp=<same one-time token>`;
- mobile landing uses the official
  `https://max.ru/<bot>?startapp=<same one-time token>`;
- after verified identify and channel-open, the Mini App calls the documented
  `window.WebApp.openMaxLink(target_url)`; location replacement remains only a
  fallback when MAX Bridge is unavailable.

Fresh production Chrome result:

```text
LANDING_CLICK_REQUIRED = NO
WEB_STARTAPP_CONSUMED = YES
SIGNED_MAX_IDENTITY_RECEIVED = YES
MAX_IDENTITY_LINK_CREATED = YES
CHANNEL_OPEN_EVENT = YES
MINI_APP_IFRAME_LEFT_OPEN = NO
TOP_LEVEL_MAX_CHANNEL = Москва 365
TOP_LEVEL_CHANNEL_OPENED_AUTOMATICALLY = YES
USER_CLICK_BEFORE_CHANNEL = 0
```

The authenticated verification user was already an owner/member, so the
channel did not show `Подать заявку`. The previously proven second-user flow
remains the evidence for the closed-channel request and official `user_added`
attribution stage.

Verification:

```text
CONTAINER_TARGETED_TESTS = 22 PASS
API_HEALTH = ready / max_mode=real
API_MAX_RUN_POLLING = false
WORKER_MAX_RUN_POLLING = true
LATEST_VISIT_EVENTS = MAX_REDIRECT_SENT,max_identity_linked,channel_open_clicked
LATEST_VISIT_CONVERSION_ROWS = 0
YANDEX_SEND = DISABLED / NOT PERFORMED
LEGACY_VPS_RECHECK = BLOCKED (available SSH identities rejected)
```
