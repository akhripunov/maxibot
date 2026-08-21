# Dev handoff: безопасное auto-connect каналов через PRIMARY `bot_added`

Дата: 2026-08-21

## Что сделано

- PRIMARY `bot_added` получает `chat_id` и вызывает официальный `GET /chats/{chat_id}`.
- Добавлен fail-closed validator: `chat/type=channel`, официальный owner совпадает с initiator, официальный `link` непустой и валиден.
- Новый канал автоматически создаёт только проверенный flow: `Admin`, `Channel`, `TrafficProfile`, `LandingPage` и `ChannelSlugRegistry`.
- Канал получает `max_connection_kind=PRIMARY`; фиктивный `BotConnection` не создаётся.
- Повторный `bot_added` идемпотентен.
- Канал другого owner не перебиндится.
- Custom `BotConnection` binding не перезаписывается.
- После нового подключения владелец получает уведомление, а экран «Мои каналы» обновляется.
- Inbox/group flow через пересылку не менялся.

## Файлы

- `app/bot/menu.py`
- `app/bot/runtime.py`
- `tests/test_bot_added_auto_connect.py`
- `tests/test_max_channel_sync.py`
- `research/DEV_HANDOFF_BOT_ADDED_AUTO_CONNECT_2026-08-21.md`

## Миграции и env

- Новых миграций нет: используется существующий `Channel.max_connection_kind`.
- Новых env-переменных нет.
- Yandex, attribution, traffic funnel и Mini App не менялись.

## Тесты

- `20 passed`:
  - auto-connect contract checks;
  - owner mismatch/non-channel/missing-invalid link fail-closed;
  - duplicate/conflict/custom binding checks;
  - existing MAX connection tests.
- Compile check `app/bot/menu.py app/bot/runtime.py`: PASS.
- Расширенный локальный набор на системном Python 3.9 не собрался: проект требует Python >=3.12; это environment blocker, не regression.
- Изолированный Docker test run на workstation не стартовал из-за недоступности Docker Hub `python:3.12-slim`.

## Production/VPS

- Deploy выполнен только на `135.106.166.46`, API и единственный worker.
- API health: PASS.
- `MAX_RUN_POLLING=false` на API: PASS.
- `MAX_RUN_POLLING=true` на worker: PASS.
- Worker startup: PASS (`MAX polling worker started`).
- New handler present in both running API and worker images: PASS.
- Local API health after final restart: PASS.
- External HTTPS health smoke after final restart: BLOCKED by transient TLS handshake failure from the current workstation; no application error was observed locally.
- Production DB/config/working channels не изменялись вручную.
- Legacy VPS `37.252.10.67`: BLOCKED — SSH access rejected by all available keys; state was not changed.

## E2E

- Реальный новый owner-controlled `bot_added` после deploy не выполнялся, поэтому production E2E auto-create остаётся `BLOCKED`.
- Точный остаточный blocker: выполнить одно реальное добавление PRIMARY MAXIBOT владельцем тестового/разрешённого канала и проверить событие, официальный metadata response, появление Channel/Profile/Landing и уведомление владельца.
- Yandex conversion и traffic attribution в рамках этой задачи не запускались.

## Follow-up: `Петербург 365` / `bot_added` (2026-08-21)

Read-only production evidence:

- the prior channel deletion completed for `-77594241122431`;
- PRIMARY worker received a new `bot_added` for `-77458971803775` with `initiator_present=true`;
- the worker called official `GET /chats/{chat_id}` and received HTTP 200;
- the application then rejected the event with the combined reason
  `official_channel_owner_or_link_check_failed`.

This log line does **not** distinguish an owner mismatch from a missing/invalid
official link. No owner ID, link, token, or raw update was logged, by design.
The event has already been consumed, and the production SSH connection became
unstable before a second safe metadata read could be completed. Therefore the
exact runtime cause is not proven and no security check was weakened.

The official MAX contract documents both relevant fields as nullable: `GET
/chats/{chatId}` may return `owner_id` and `link`, while `bot_added` contains
the `user` who added the bot and `is_channel`. See [GET /chats/{chatId}](https://dev.max.ru/docs-api/methods/GET/chats/-chatId-),
[Update.bot_added](https://dev.max.ru/docs-api/objects/Update), and [Chat](https://dev.max.ru/docs-api/objects/Chat).

Follow-up status: **BLOCKED** pending one safe re-read of the metadata for
`-77458971803775` (or one owner-controlled re-add event) with only field
presence/type/validity and masked equality diagnostics. No production code,
DB records, channels, attribution, forwarding, or Yandex settings were
changed in this follow-up.

### Fresh metadata read and diagnostics deploy

Fresh read-only `GET /chats/-77458971803775` returned the following safe shape:

| Fact | Result |
|---|---|
| `type` | `channel` |
| `owner_id` | present, integer |
| `link` | present, string |
| API status | `200` |

Thus the current blocker is not explained by missing `owner_id` or missing
`link`. Equality with the `bot_added.user` initiator and URL validity still
need to be observed on the next event.

Added masked diagnostics to the existing rejection path. It logs only field
presence/type, channel type, `owner_matches_initiator`, and `link_valid`; raw
owner IDs, links, tokens, cookies, and frames are never logged. Targeted tests:
`21 passed`. Production diagnostic deploy completed with API healthy and the
single-poller rule verified (`API=false`, `worker=true`).

Remaining E2E blocker: owner must perform one more remove/add cycle for
`-77458971803775`; no DB substitution or security bypass is permitted.

### Admin-role diagnostic follow-up

The fresh official `GET /chats/{chat_id}` response had `owner_present=false`
but a valid official link. A read-only `GET /chats/{chat_id}/members/admins`
returned two entries, including the documented `is_owner` field. The current
code now logs only `initiator_in_admins` and `initiator_is_owner` in addition
to the existing masked facts; it still rejects when `owner_id` is absent.

The diagnostic deploy is live on the primary VPS and targeted tests remain
`21 passed`. No new `bot_added` has arrived after this deploy yet. One fresh
remove/add cycle is required to capture the two admin-role booleans. If the
initiator is the official owner in that response, the minimal safe fix is to
use that official owner-role match only when `GET /chats` omits `owner_id`;
otherwise the fail-closed rejection remains correct.

### Final follow-up result

The next owner remove/add cycle produced:

| Diagnostic | Result |
|---|---|
| `/chats` status | `200` |
| channel type | `channel` |
| `owner_id` | absent |
| official `link` | present and valid |
| `/members/admins` | `403` |

The admin diagnostic initially surfaced the `403` as a sync exception. That
was corrected so diagnostic access is optional and the normal fail-closed
rejection remains intact; no channel can be created from an unproven owner.
The correction is deployed and the API is healthy. Targeted tests remain
`21 passed`.

Exact remaining blocker: MAX Bot API exposes neither `owner_id` for this
private channel nor an accessible admin-role response to the bot. The
`bot_added.user` field proves who added the bot, but the official contract does
not by itself prove that this user owns the channel. Accepting it as owner
would weaken the required security contract, so no auto-connect fix is safe
yet. Real E2E remains **BLOCKED** pending an official owner proof/API access
or an architect-approved contract change.

### Approved contract change and deploy

The architect explicitly approved `bot_added.user` as the authorized
connector signal for this auto-connect business flow when the event comes from
the PRIMARY client and official `GET /chats/{chat_id}` confirms
`type=channel` plus a valid non-empty official `link`. The implementation now:

- no longer requires `owner_id` or `/members/admins`;
- binds a newly created channel to the `bot_added.user` signal;
- continues to reject missing/invalid links and non-channel events;
- continues to reject saved-owner conflicts, preserve custom BotConnection,
  and keep duplicate events idempotent.

Targeted tests: `21 passed`. Production deploy completed on `135.106.166.46`;
API is healthy, API polling is `false`, and the sole worker polling is
`true`. No Yandex, attribution, landing, inbox, or manual DB changes were
made.

Post-deploy REAL E2E still requires one fresh owner remove/add cycle for
`-77458971803775`; the preceding event occurred before this contract deploy.

### Final production E2E

The owner performed the required remove/add cycle after the approved deploy.
Production worker evidence:

- `GET /chats/-77458971803775` returned `200`;
- `type=channel`, official link present and valid;
- `MAX_CHANNEL_AUTO_CONNECT status=created`;
- owner-proof fields were absent, as expected under the approved contract;
- owner notification `POST /messages` returned `200`;
- the worker edited the `Мои каналы` screen successfully, showing `Петербург 365`.

The worker remained the only polling process on the primary VPS. API health
was healthy, API polling was `false`, and worker polling was `true`.

Final statuses:

| Item | Status |
|---|---|
| Official PRIMARY `bot_added` | PASS |
| Channel type validation | PASS |
| Official link validation | PASS |
| Owner-proof-absent approved contract | PASS |
| New Channel/Profile/Landing creation | PASS |
| Owner binding from authorized connector signal | PASS |
| Owner notification | PASS |
| `Мои каналы` refresh | PASS |
| Duplicate/conflict/custom-bot regressions | PASS (`21 passed`) |
| Real production remove/add E2E | PASS |
| Yandex/attribution/landing/inbox changes | NONE |

## Итоговые статусы

| Пункт | Статус |
|---|---|
| Valid owner auto-create | PASS по коду/контрактам, production E2E BLOCKED |
| Owner mismatch reject | PASS |
| Existing owner conflict reject | PASS |
| Duplicate idempotency | PASS по application contract |
| Missing/invalid link fail-closed | PASS |
| Custom BotConnection backward compatibility | PASS по regression tests |
| Production API/worker deploy | PASS |
| Legacy VPS verification | BLOCKED |
| Real production bot_added E2E | BLOCKED |
