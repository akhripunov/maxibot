# MAXIBOT Dev handoff — 2026-08-15

## 1. Сделано

- Усилен callback-маршрут `settings:<channel_id>`: он теперь явно проходит через navigation-ветку, разрешает канал в scope владельца и редактирует сервисное сообщение с экраном настроек.
- Добавлен поведенческий regression test для callback «Настройка сообщений», включая owner-scoped resolution и edit исходного service message.
- `/admin` проверен по текущему runtime: распознаются `/admin`, `/admin@bot`, выполняются `is_admin`, очистка навигации/support state и render `owner_admin`. В текущем workspace отдельной причины в обработчике не найдено; VPS E2E команды в MAX не выполнено.
- Выполнен публичный black-box A/B/C прогон TeleGraphyx. Ни один чужой кабинет, токен, cookie, raw frame или персональный идентификатор не использован.

## 2. Изменённые файлы и миграции

Изменены:

- `app/bot/runtime.py`
- `tests/test_runtime_entrypoints.py`

Добавлен research handoff:

- `research/DEV_HANDOFF_2026-08-15.md`

Миграции: **нет**.

## 3. Env / запуск

Новых переменных нет. Сохраняются существующие требования:

- `MAX_RUN_POLLING=false` для API и локального compose;
- `MAX_RUN_POLLING=true` только для выделенного VPS worker;
- `TRAFFIC_BOT_ATTRIBUTION_ENABLED=true` на production API;
- `MAX_BOT_TOKEN`, `DATABASE_URL`, OAuth/Yandex secrets — только из защищённого env, значения не публиковались.

## 4. Тесты и E2E

- `py_compile` для всех `app/` и `tests/`: **PASS** на Python 3.11.
- `pytest -q` системным Python 3.9: **BLOCKED** — проект использует синтаксис Python 3.10+, collection падает до тестов.
- `pytest` на доступном Python 3.11: **BLOCKED** — пакет pytest отсутствует.
- Docker build QA: **BLOCKED** — TLS timeout при получении `python:3.12-slim` из registry.
- `https://go.maxibot.jobjin.ru/health/ready`: **PASS** в повторном запросе, `status=ready`, `max_mode=real`.
- `https://go.maxibot.jobjin.ru/health/live`: **PASS** в отдельном запросе.
- `https://maxibot.jobjin.ru/health/ready`: **BLOCKED** — TLS/SSL failure.
- Реальный MAX E2E для `/admin` и «Настройка сообщений»: **BLOCKED** — нет доступного owner-controlled MAX аккаунта/тестовой сессии в этом workspace.
- Production deploy: **не выполнялся**.

## 5. Attribution black-box matrix

Статусы означают: PROVEN — наблюдалось в контролируемом тесте; CLAIMED — только заявлено публичными материалами; NOT_PROVEN — доказательства недостаточно; BLOCKED — нужен собственный кабинет/канал/API/event access.

| Сервис | A/B/C | Фактический entry/redirect | MAX user_id | join/member | source→user | yclid/UTM | unsubscribe | uniqueness | Итог |
|---|---|---|---|---|---|---|---|---|---|
| TeleGraphyx | A/B/C public | landing → `max.ru/<bot>?startapp=<masked>`; A/B/C дали одинаковый final URL | NOT_PROVEN | NOT_PROVEN | NOT_PROVEN | NOT_PROVEN | NOT_PROVEN | SINGLE_SHARED_INVITE/UNKNOWN; visitor-unique token не доказан | PASS для route, BLOCKED для exact attribution |
| tgTrack / «Откуда Подписки» | BLOCKED | Публично заявлен tracking link/landing→bot/channel | BLOCKED | CLAIMED | CLAIMED | CLAIMED | CLAIMED | CLAIMED per source, visitor-level NOT_PROVEN | BLOCKED |
| Linker | BLOCKED | Публичный MAX bot deep link с `start` payload | NOT_PROVEN | CLAIMED | CLAIMED | CLAIMED | CLAIMED | UNKNOWN | BLOCKED |
| Gatekeeper / «гейткипер» | BLOCKED | Публичный bot deep link с `start=t_link_<id>` | NOT_PROVEN | CLAIMED | CLAIMED | CLAIMED | CLAIMED | UNKNOWN | BLOCKED |
| Mobzio | BLOCKED | Заявлена схема link→bot→subscription | CLAIMED, поле не раскрыто | CLAIMED | CLAIMED | NOT_PROVEN | NOT_PROVEN | CLAIMED per link/source | BLOCKED |
| BotHelp | BLOCKED | MAX-specific customer redirect/event chain публично не раскрыта | NOT_PROVEN | CLAIMED generic analytics | NOT_PROVEN | CLAIMED generic UTM, exact yclid NOT_PROVEN | CLAIMED generic analytics | UNKNOWN | BLOCKED |
| Quescha | BLOCKED | Документированы MAX WebApp `startapp`, session/client/referral primitives | PARTIAL | NOT_PROVEN for channel member | NOT_PROVEN | NOT_PROVEN | NOT_PROVEN | CLAIMED primitives only | BLOCKED |

TeleGraphyx evidence: three clean browser tabs opened the same public landing with distinct test query markers; all resolved to the same bot/startapp URL. The payload was not emitted in this report. This proves the public route only. It does not prove membership, MAX identity correlation, source persistence, or Yandex conversion.

## 6. Can the mechanisms be applied to MAXIBOT?

Yes, as an architecture pattern only: landing stores `yclid/UTM/click_id`, then an opaque bot deep link binds `MAX user_id`, then a verified channel membership event is required before `userDidSubscribe`. No external service in this run provides enough evidence to import its exact implementation. Direct shared native invite remains insufficient for exact attribution.

## 7. Final statuses

| Item | Status |
|---|---|
| Settings callback fix + regression test | PASS (runtime test execution BLOCKED by environment) |
| `/admin` source/runtime path | PASS in code inspection; VPS E2E BLOCKED |
| TeleGraphyx public route | PASS / exact attribution BLOCKED |
| tgTrack | BLOCKED |
| Linker | BLOCKED |
| Gatekeeper | BLOCKED |
| Mobzio | BLOCKED |
| BotHelp | BLOCKED |
| Quescha | BLOCKED |
| VPS health on `go.maxibot.jobjin.ru` | PASS |
| Old `maxibot.jobjin.ru` TLS/DNS path | BLOCKED |
| Production deploy/change | PASS — no deploy/change performed |

## 8. Exact next blocker for architect

Provide owner-controlled MAX test identities, one test channel, and valid test access for each attribution service that supports MAX. For each service run fresh A/B/C links and capture only sanitized event fields: masked `MAX user_id`, masked `channel_id`, source/link id, membership status and timestamps. For MAXIBOT itself provide a current owner MAX session and deployment access before declaring `/admin` or settings fixed by real E2E.
