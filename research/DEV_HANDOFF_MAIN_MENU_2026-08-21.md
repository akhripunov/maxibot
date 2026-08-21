# Handoff: MAXIBOT main menu update

Date: 2026-08-21

## Result

Главное меню теперь имеет порядок:

1. `📢 Подключить канал` → `connect_channel`
2. `📣 Продвижение` → `promotion`
3. `📩 Обращения / Предложка` → `channels`
4. `🤖 Мои боты` → `my_bots`
5. `ℹ️ О сервисе` → `about`

The new channel screen contains the exact architect-approved setup text and
does not mention TeleGraphyx or message forwarding. Existing `channels`
callback and channel/inbox functionality remain unchanged.

## Files

- `app/bot/menu.py`
- `app/bot/runtime.py`
- `app/domain/services.py`
- `tests/test_bot_added_auto_connect.py`

No migrations or new environment variables.

## Tests

- UI/contract plus MAX connection targeted tests: `24 passed`.
- Compilation: PASS.
- Broader imports on workstation Python 3.9 remain unavailable because the
  project requires Python >=3.12; this is the existing environment limitation.

## Production

- Deploy: PASS on `135.106.166.46`.
- Running worker image contains the new `📢 Подключить канал` menu.
- API health: PASS.
- API `MAX_RUN_POLLING=false`.
- Sole worker `MAX_RUN_POLLING=true`.
- Existing channel auto-connect, group-chat contract, attribution, Yandex,
  landing, and traffic flow were not changed by this task.

## Status

| Item | Status |
|---|---|
| Main-menu order | PASS |
| New channel setup screen | PASS |
| `channels` callback compatibility | PASS |
| Production deploy | PASS |
| Single poller rule | PASS |
| Real UI browser smoke | BLOCKED — requires owner/browser session |

## Text correction follow-up

The setup screen now uses the exact approved wording `Перейдите в раздел
«Подписчики»` in step 2. The old `Подписатели` wording is absent.

- Exact UI/contract tests: `25 passed`.
- Production worker contains the corrected text: PASS.
- API health: PASS.
- API polling `false`, sole worker polling `true`: PASS.

## Add-channel button label follow-up

The add-channel buttons now use the exact shared label `📣 Подключить ещё` in
Promotion and in `Обращения / Предложка`, including the post-delete/empty-list
states. Callback data remains unchanged:

- Promotion: `promotion_connect`
- Обращения / Предложка: `add_channel`

The legacy labels `➕ Добавить канал` and `➕ Добавить ещё` are absent from the
menu. Exact UI tests: `27 passed`. Production worker contains the new label;
API is healthy, API polling is `false`, and the sole worker polling is `true`.

## Main-screen copy follow-up

The home text now exactly follows the approved copy, including:

- `📣 Продвижение` description;
- `📩 Обращения / Предложка` description;
- `🤖 Мои боты` description;
- dynamic `📚 Каналов подключено: {channels_count}`;
- dynamic `🤖 Собственных ботов: {connected_bots_count}`.

The old descriptive text and the former accepting-message counter are no
longer rendered. A runtime assertion in the production Python 3.12 image
passed (`MAIN_MENU_EXACT=PASS`). Production API is healthy and single-poller
flags remain unchanged.

## Promotion screen copy follow-up

The exact approved promotion copy is centralized in
`promotion_intro_text()` and used both for channel selection and
`promotion_connect`. Buttons and callback data are unchanged. Targeted exact
UI tests: `28 passed`. Production worker contains the helper; API is healthy,
API polling is `false`, and the sole worker polling is `true`.

## Compact main-screen copy follow-up

The main screen now uses the approved compact layout with two spaces after the
emoji labels and hyphenated descriptions. Dynamic values remain in
`Каналов подключено` and `Собственных ботов`; the old accepting counter is not
rendered. Local targeted tests: `28 passed`; production worker contains the
compact copy, API is healthy, API polling is `false`, and the sole worker
polling is `true`.

## Shared connection-instructions helper

The exact approved copy is now centralized in
`app.bot.menu.channel_connection_instructions()` and reused by all channel
connection entry points:

- main menu `📢 Подключить канал`;
- promotion empty state and `➕ Подключить ещё`;
- `📩 Обращения / Предложка` `➕ Добавить канал` and empty state.

No legacy forwarding/TeleGraphyx text remains in these paths. Exact helper
contract tests: `26 passed`. Production worker contains the helper, API is
healthy, API polling is `false`, and the sole worker polling is `true`.

## Home banner

Added the optimized home-only banner asset:

- Asset: `app/static/images/maxibot_home_banner_4x3.webp`
- Runtime URL: `{TRAFFIC_PUBLIC_BASE_URL}/static/images/maxibot_home_banner_4x3.webp`
- Size: 57 KB; dimensions 1200×900; WEBP; 4:3
- The artwork is re-laid out inside a full designed 4:3 canvas so MAX preview
  does not crop the left/right edges and no letterbox bars are added. The
  robot, complete MAXIBOT mark and slogan remain present.
- The asset is attached only when `MenuRepository.render(..., screen="home")`
  is called. Promotion, Обращения/Предложка, Мои боты and all other screens
  continue to return an empty media list.
- Existing edit/send and persisted service-message flow is unchanged; no
  additional message is created by the banner integration.

Contract test: `tests/test_bot_added_auto_connect.py` — `14 passed` in the
available local Python 3.9 environment. Full runtime suites requiring Python
3.12 were not run locally because the host interpreter is Python 3.9.

Production deploy: PASS on `135.106.166.46`.

- API container: healthy; `/health/ready` returned 200 in container logs.
- API `MAX_RUN_POLLING=false`.
- Sole worker `MAX_RUN_POLLING=true` and active.
- Banner file is present in the production API image at 45 KB.
- Legacy VPS worker check: BLOCKED — SSH access to `37.252.10.67` was denied
  in this run; no claim is made beyond the previously recorded single-poller
  state.

### Padding-crop follow-up

The banner was corrected by re-laying out the full artwork into a 4:3 canvas
and publishing it under a new filename to avoid MAX media caching. The
deployed asset is now 1200×900, 57 KB, and is served by the production API with
HTTP 200. API remains healthy;
`MAX_RUN_POLLING=false` on API and `true` on the sole worker.

## Promotion connect copy

`promotion` keeps the detailed `promotion_intro_text()` explaining promotion,
landing pages, Yandex Direct and conversion learning. The separate
`promotion_connect` screen now uses `promotion_connect_text()` with the exact
short channel-connection instructions approved by the architect. Its callback
and auto-connect behavior are unchanged; the Appeals/Предложка flow is
untouched.

Targeted contract tests: `15 passed`.

## Full-bleed banner asset follow-up

The workspace-provided compact replacement is deployed under the cache-busted
asset `app/static/images/maxibot_home_banner_compact.webp`: 1402×1122 WEBP
(approximately 5:4), with the
full-bleed dark canvas and complete robot/MAXIBOT/tagline artwork. No menu,
attribution or Yandex code was changed.

- `tests/test_bot_added_auto_connect.py -k home_banner`: PASS (`1 passed`).
- Production API: healthy.
- API polling: `false`; sole worker polling: `true`.
- Production `/static/images/maxibot_home_banner_compact.webp`: HTTP 200
  (96,620 bytes) from inside the API container.
- Fresh owner MAX message visual verification: BLOCKED in this run because no
  owner MAX browser session was available; no false E2E PASS claimed.

## Temporary banner disablement

Per architect decision, active menu media delivery is temporarily disabled:
`MenuRepository.render()` now returns `media=[]` for every screen, including
home. Static banner files and the helper remain available for a future,
approved return. Menu text, buttons, callbacks, attribution and Yandex are
unchanged.

- Targeted contract tests: `16 passed`.
- Production API: healthy; API polling `false`; sole worker polling `true`.

## Custom bot token refresh fix

Root cause: the refresh callback persisted `custom_bot_state="refresh"` and
the bot id in `pending_custom_bot_id`, while message handling only accepted
the obsolete `refresh:<bot_id>` form. Submitted replacement tokens therefore
never reached the MAX `/me` probe.

The handler now accepts the compact persisted `refresh` state, probes the
submitted credential without logging it, updates the selected bot, and renders
the existing ACTIVE / START_REQUIRED / ERROR result screens. Primary MAXIBOT,
attribution and Yandex are unchanged.

- Targeted regression/source contract tests: `17 passed`.
- Production deploy: pending completion of the current normal deploy check.
