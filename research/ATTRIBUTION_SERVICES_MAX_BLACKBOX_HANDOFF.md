# Black-box attribution research: services → MAX

Дата исследования: 2026-08-15  
Статус: sanitized research-only handoff; production-код и funnel не изменялись.

## Краткий вывод

Публично подтверждается общий паттерн: рекламная ссылка/лендинг → tracking redirect или MAX-бот → идентификация пользователя ботом → переход в канал → подписка. Однако для большинства сервисов публичные материалы не раскрывают конкретное поле, которое связывает рекламный визит с `MAX user_id`, а доступ к событиям и кабинетам требует собственного аккаунта/канала.

Единственный фактически наблюдённый public flow в этом исследовании — TeleGraphyx: landing переходит в MAX bot deep link с `startapp`. Это доказывает маршрут, но не доказывает exact `visitor/yclid → MAX user_id → membership` mapping. Для MAXIBOT это означает: tracking deep link бота остаётся технически обоснованным вариантом; прямой переход общей invite-ссылкой не даёт доказанной точной атрибуции.

## Классификация

- **PROVEN** — наблюдалось в контролируемом тесте или подтверждается доступным техническим событием.
- **CLAIMED** — заявлено сервисом/документацией, но поле и полный runtime-flow не проверены.
- **NOT_PROVEN** — по публичным материалам доказательства недостаточно.
- **BLOCKED** — требуется собственный кабинет, канал, API key, бот-события или ручное действие владельца.

## Сводная таблица

| service | entry URL | redirect type | final URL pattern | token type | uniqueness | MAX user identity proof | membership proof | yclid/UTM proof | unsubscribe proof | confidence | blockers |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TeleGraphyx | `https://max.tgryx.ru/<public_id>` | landing → MAX bot deep link | `https://max.ru/<bot>?startapp=<payload>` | `startapp`, наблюдался payload вида `client_<opaque>` | `UNKNOWN`; два public открытия дали один и тот же final URL | `NOT_PROVEN` | `NOT_PROVEN` | `NOT_PROVEN` | `NOT_PROVEN` | public route — medium; exact attribution — low | private cabinet/API/events unavailable |
| tgTrack / «Откуда Подписки» | `https://tgtrack.ru/` | claimed tracking link/landing → bot or channel | public MAX bot entry exists; exact customer redirect unavailable | service link + UTM/source; exact MAX payload undisclosed | `CLAIMED` per source/link; per-visitor `NOT_PROVEN` | `BLOCKED` | `CLAIMED` | `CLAIMED` | `CLAIMED` | claims — medium; evidence — low | API events require key; own channel/cabinet unavailable |
| Linker | `https://max.ru/id362803009508_3_bot?start=2g4` | MAX bot deep link | same bot with `start=<token>` | `start` token, public example `2g4` | `CLAIMED` smart-link level; visitor level `UNKNOWN` | `NOT_PROVEN` | `CLAIMED` | `CLAIMED` | `CLAIMED` | low | no public event payload or owned test channel |
| Gatekeeper | `https://max.ru/id550368365124_3_bot?start=t_link_1775407141` | MAX bot deep link / short tracking link | same bot with `start=t_link_<id>` | link identifier in `start` | `UNKNOWN` | `NOT_PROVEN` | `CLAIMED` | `CLAIMED` | `CLAIMED` | low | marketing page only; private event path unavailable |
| Mobzio | `https://mobz.io/max-podpiski-analytics` | claimed tracking deeplink → bot → channel | exact customer URL not public | service deeplink payload; exact format undisclosed | `CLAIMED` per link/source; per visitor `NOT_PROVEN` | `CLAIMED` bot/webhook path, field undisclosed | `CLAIMED` | `NOT_PROVEN` for exact yclid field | `NOT_PROVEN` in MAX-specific evidence | architecture — medium; proof — low | own bot/channel and integration credentials unavailable |
| BotHelp | `https://bothelp.io/ru/max` | bot/mini-landing flow; MAX-specific redirect not publicly specified | no public channel attribution URL pattern | generic UTM labels/ClientID; MAX payload undisclosed | `UNKNOWN` | `NOT_PROVEN` | `CLAIMED` generic analytics | `CLAIMED` generic UTM analytics; exact yclid mapping `NOT_PROVEN` | `CLAIMED` generic analytics | low/medium | no public MAX channel E2E or event schema |
| Quescha | `https://quescha.ru/` | bot landing/WebApp/referral flow | MAX WebApp supports `?startapp=<start_param>` | `start_param`, WebApp session, ClientID/Messenger, referral link | `CLAIMED` referral/session level; ad visitor level `NOT_PROVEN` | `PARTIAL` session/client primitives documented; channel-member binding not shown | `NOT_PROVEN` for MAX channel | `NOT_PROVEN` | `NOT_PROVEN` | primitives — medium; exact funnel — low | no public ad→MAX channel E2E |

## Detailed evidence

### TeleGraphyx

Public controlled observation used the same public landing twice:

```text
https://max.tgryx.ru/<public_id>
  → https://max.ru/id510107251374_bot?startapp=<sanitized_payload>
```

The final URL was a MAX bot deep link, not a native `join/<invite>` URL. Two independent public openings of the same landing produced the same final bot/startapp route. Therefore:

- `landing → MAX bot` is **PROVEN** for the public sample;
- a visitor-unique token is **NOT_PROVEN**;
- a native individual invite is **NOT_PROVEN**;
- the concrete field linking `yclid` or visit ID to `MAX user_id` is **NOT_PROVEN**;
- membership and unsubscribe evidence were not available without a private owner session.

The public product page describes channel analytics and source labels, but a marketing statement is not an event-level proof. Public frontend routes expose dashboard concepts such as sources and subscriptions, while the actual authenticated data path was not accessible in this test.

### tgTrack / «Откуда Подписки»

The public site describes a bot being added as channel administrator, a tracking link/landing retaining click parameters, redirecting the visitor, and tracking clicks, subscriptions and unsubscribes. It also describes Yandex/offline-conversion support. The public API test page requires an API key, so no event payload or source-to-user field was inspected.

Result: the architecture is **CLAIMED**, but `click_id/source_id → MAX user_id → membership` and the exact yclid storage field are **NOT_PROVEN/BLOCKED**.

### Linker

The public MAX bot entry contains a `start` payload and publicly advertises smart links, click/subscription/unsubscribe counting. This establishes a bot deep-link surface and a claimed link-level tracking mechanism. It does not establish whether the token is per visitor, per campaign, or merely per generated link, and does not expose the membership event or a yclid field.

### Gatekeeper

The public MAX bot entry contains a `start=t_link_<id>`-shaped value. The public page advertises short links, subscription checks, unsubscribe analytics and source analytics. No public source exposed the event schema or demonstrated a two-user A/B mapping, so the claims remain **CLAIMED**.

### Mobzio

Public MAX material explicitly describes the limitation of a direct channel link: it cannot identify who subscribed after a particular click. The described solution is `link → bot → subscription`, with a MAX bot, bot token/webhook and channel integration. This supports the same architectural direction as MAXIBOT’s opaque bot deep link, but the public pages do not reveal the actual payload field, storage key, or member correlation event.

### BotHelp

Public documentation describes analytics such as subscriber counts, unsubscribe status, subscription time, goals and UTM labels. The MAX product page confirms MAX support, but no public MAX-specific channel E2E exposed `MAX user_id`, invite/source binding, or exact yclid handling. Generic BotHelp analytics must therefore not be treated as proof for this MAX funnel.

### Quescha

Public documentation gives useful primitives: a subscriber `ClientID`, messenger value `max`, MAX WebApp `start_param` passed through `?startapp=...`, a WebApp session object, and referral links. These prove that Quescha can carry opaque/session data in a MAX bot/WebApp context. They do not prove that an ad visitor can be linked to a later MAX channel membership or that yclid is preserved into that membership.

## Controlled A/B/C test status

| service | A/B/C public or owned sessions | result |
|---|---|---|
| TeleGraphyx | Public A/B repeated opening of one public landing; no private accounts | final bot route repeated; no visitor-unique token proven; no user/member proof |
| tgTrack | No owned cabinet/channel/API key in the allowed session | `BLOCKED` |
| Linker | Public bot page only; no owned channel and no two MAX identities | `NOT_TESTED` |
| Gatekeeper | Public bot page only; no owned channel and no two MAX identities | `NOT_TESTED` |
| Mobzio | Public documentation only; no owned integration credentials | `BLOCKED` |
| BotHelp | Public documentation only; no owned MAX channel flow | `BLOCKED` |
| Quescha | Public documentation only; no owned MAX channel flow | `BLOCKED` |

No public page was treated as a substitute for a controlled A/B/C test. No чужие credentials, cookies, local storage, raw frames, production approvals, or MAX opcodes were used.

## Implication for MAXIBOT

Recommended evidence-based design:

```text
Yandex ad
  → MAXIBOT landing (save yclid/ClientID/UTM)
  → opaque one-time bot deep link
  → bot_started(user_id, payload)
  → bot sends target of the bound channel
  → join request / manual approval
  → verified membership(user_id, channel_id)
  → conservative source attribution
  → userDidSubscribe only when the mapping is proven
```

Direct landing → one shared native channel invite can measure a transition, but cannot be declared exact `yclid → MAX user_id` attribution unless MAX supplies a source/referrer or an independently bound user identity. Do not infer per-visitor native invites from the public claims of these services.

## Files, commands, and safety

Changed files: this report only:

- `research/ATTRIBUTION_SERVICES_MAX_BLACKBOX_HANDOFF.md`

No application, database, migration, worker, Attribution Engine, landing funnel, MAX transport or production flag was changed. Auto-approve and opcode 77 were not touched.

Research actions were read-only public web inspection and comparison of already sanitized MAXIBOT research artifacts. No private ignored capture directory was copied or included. No real conversion was sent.

Suggested verification commands:

```bash
rg -n "TeleGraphyx|tgTrack|Linker|Gatekeeper|Mobzio|BotHelp|Quescha" research/ATTRIBUTION_SERVICES_MAX_BLACKBOX_HANDOFF.md
rg -n "(Authorization:|Bearer |MAX_BOT_TOKEN|CLIENT_SECRET|Set-Cookie:|raw frame)" research/ATTRIBUTION_SERVICES_MAX_BLACKBOX_HANDOFF.md
```

The second command is a prose-safety check; public example URLs are sanitized and no credentials are included.

## Final status

```text
INDIVIDUAL_INVITE = NOT_PROVEN
TELEGRAPHYX_MECHANISM = PARTIAL
TGTRACK_MECHANISM = CLAIMED / BLOCKED_FOR_PRIVATE_PROOF
LINKER_MECHANISM = CLAIMED / NOT_PROVEN
GATEKEEPER_MECHANISM = CLAIMED / NOT_PROVEN
MOBZIO_MECHANISM = CLAIMED / NOT_PROVEN
BOTHELP_MECHANISM = CLAIMED / NOT_PROVEN
QUESCHA_MECHANISM = PARTIAL_PRIMITIVES / NOT_PROVEN_FOR_MEMBERSHIP
EXACT_ATTRIBUTION_READY = NO
AUTO_APPROVE_TOUCHED = NO
PRODUCTION_CHANGED = NO
```

Concrete next step for a real proof: use two owner-controlled MAX identities and one owner-controlled test channel per service that supports MAX, create fresh A/B/C tracking links through that service, capture only sanitized webhook/event fields (`source/link id`, `MAX user_id` masked, `chat_id` masked, status and timestamps), then check whether the same source key survives through manual membership. Without those accounts and service access, the correct status remains `NOT_PROVEN/BLOCKED`, not WORKING.

## Дополнительная проверка публичных MAX-механик (2026-08-16)

### «Откуда подписчик»

Публичная MAX-точка входа сервиса имеет вид bot deep link с `startapp`, а не
прямой общей ссылки закрытого канала. Публичная страница показывает бота и
описание «перенаправит вас в нужный канал»; это доказывает наличие bot/WebApp
bridge, но не раскрывает payload, поле source или событие membership.
[Публичная MAX-точка входа «Откуда Подписки»](https://max.ru/id503501079307_1_bot?startapp=TLc1c6a7cbf8d4)

Статус: `BOT/STARTAPP_BRIDGE = PROVEN`, `EXACT_USER_ATTRIBUTION = NOT_PROVEN`.

### TeleGraphyx

Публичная документация описывает: бот добавляется администратором канала,
создаётся лендинг, запускается реклама, а сервис заявляет отчёты о том, кто
пришёл, откуда пришёл, подписках/отписках, передачу целей в Метрику и
автоприём заявок. Это подтверждает архитектурный паттерн «бот-администратор +
tracking layer + membership event», но не раскрывает exact mapping
`yclid/source → MAX user_id`.
[Страница Traffic TeleGraphyx](https://telegraphyx.ru/traffic/)

Статус: `CHANNEL_BOT_ADMIN = CLAIMED`, `MEMBERSHIP/UNSUBSCRIBE = CLAIMED`,
`YANDEX = CLAIMED`, `EXACT_SOURCE_TO_USER_FIELD = NOT_PROVEN`.

### Что можно перенести в MAXIBOT

PROVEN/переносимо:

- уникальный opaque payload на каждый рекламный визит;
- tracking deep link MAX-бота;
- server-side bind `bot_started(user_id, payload)`;
- channel-scoped target, полученный через `GET /chats/{chat_id}`;
- бот-администратор канала и проверка join/member по `user_id`;
- отправка `userDidSubscribe` только после доказанного bind + membership.

Нельзя переносить как доказанный факт:

- скрытое открытие канала без любого MAX-экрана;
- unique native invite для каждого посетителя;
- точный `yclid → MAX user_id` только по общей ссылке канала;
- автоприём заявок в production без отдельного E2E.

Итог: публичные сигналы обоих сервисов скорее подтверждают bot/deep-link
bridge, чем direct channel attribution. «Невидимость» у конкурентов публично
не доказана: MAX может показывать системную шторку или bot/WebApp surface в
зависимости от клиента. Для MAXIBOT принимаем только event-level доказательство,
а не маркетинговое обещание.

## Фактический проход ссылки `mx-t.su` (2026-08-16)

Собственная Chrome-сессия прошла ссылку до канала. Raw payload и персональные
данные не сохранены.

Наблюдаемая цепочка:

```text
mx-t.su/<opaque-link>
  → HTML-экран с названием целевого канала «Петербург 365 вход»
  → MAX bot deep link с `startapp=<opaque payload>`
  → MAX Web bot screen «Переход в канал от Откуда Подписки»
  → после «Начать» открывается встроенный Mini App
  → Mini App показывает «Пару секунд, переходим в Макс…»
  → автоматически открывается канал «Петербург 365 вход»
```

Фактически доказано:

- сервис использует промежуточную страницу, а не общий `join` URL;
- целевой канал известен до перехода и отображается на странице;
- используется `startapp` payload бота;
- Mini App автоматически вызывает переход в конкретный канал;
- после запуска bridge пользователь оказывается в нужном канале;
- отдельного ручного ввода ссылки канала нет.

Не доказано этим проходом:

- какое именно событие сервиса связывает opaque payload с `MAX user_id`;
- сохраняется ли `yclid` внутри их backend;
- member/join event и отписка;
- отправка конкретного `userDidSubscribe` в Яндекс.

Практический вывод для MAXIBOT: нужно повторить именно их технический паттерн —
channel-branded intermediate HTML → `startapp` → silent Mini App → channel
target. В нашем текущем runtime нельзя одновременно отправлять обычную CTA в
чат MAXIBOT и считать bridge невидимым: для этого startapp-событие должно
связывать пользователя серверно, а Mini App — сразу выполнять `identify` и
`channel-open` без сообщения/кнопки от MAXIBOT.

## Silent bridge deploy/E2E (2026-08-16)

- `app/bot/runtime.py`: для traffic `startapp` убрана видимая CTA/сообщение
  «Открыть канал». Runtime только связывает opaque payload с MAX user и
  передаёт управление Mini App.
- Production `135.106.166.46`: `api` и единственный `worker` работают на
  `maxibot-vps-api:20260816-silentbridge`; `api MAX_RUN_POLLING=false`,
  `worker MAX_RUN_POLLING=true`.
- Legacy `37.252.10.67`: `maxibot-vps-worker-1` остановлен.
- Chrome production проход: landing автоматически редиректит в MAX startapp;
  после открытия существующего MAX-чата новая CTA от MAXIBOT не появилась.
  Повторное событие `startapp → user_id` в этой сессии не считалось доказанным,
  поскольку аккаунт уже ранее запускал бота.

Статус: silent runtime deploy `PASS`; fresh-user attribution E2E `BLOCKED` до
теста на новом MAX аккаунте/чистом запуске Mini App.

## Native `max://` route (2026-08-16)

Публичный HTML `mx-t.su` дополнительно показал важную деталь: automatic
redirect использует `max://max.ru/<bot>?startapp=<opaque>` и скрытый iframe с
тем же native URL; HTTPS-ссылка предлагается отдельно как browser fallback.
Поэтому MAXIBOT переведён на тот же принцип:

- native MAX client: автоматический `max://` → attached Mini App →
  `identify` → `channel-open` → target channel;
- обычный browser: автоматический HTTPS fallback, без зависания на пустой
  технической странице;
- opaque startapp token остаётся тем же server-side visit reference, поэтому
  yclid не теряется.

Production image: `maxibot-vps-api:20260816-nativebridge4`; health `ready`.
Chrome подтверждает fallback на HTTPS bot surface; native MAX E2E пока требует
отдельного нативного клиента и нового MAX аккаунта.
