# Runtime comparison: «Откуда Подписки» vs MAXIBOT

Дата: 2026-08-17  
Исходная ссылка: `https://mx-t.su/+MX2vpZBbmb2n3b5y`  
Режим: read-only, без JOIN_REQUEST и без изменений MAXIBOT.

## Короткий результат

```text
TRACKING_URL = https://mx-t.su/+MX2vpZBbmb2n3b5y
STARTAPP_TOKEN_HASH = e6c0596be9a09e3cf28798da67c46b76e9047bf509a148cc0ab644f0fa8a744f
MAX_DEEPLINK = max://max.ru/id503501079307_1_bot?startapp=<MASKED>

MINI_APP_CONFIRMED = YES, но как отдельный bot Mini App flow
MINI_APP_URL = https://max-click.tgtrack.ru/app_bot_check.php
MAX_WEB_APP_JS_USED = YES, MAX WebApp payload observed; library source not captured

BACKEND_IDENTIFY_REQUEST_FOUND = NO
VERIFIED_OR_SIGNED_MAX_DATA_SENT = NOT_PROVEN
START_TOKEN_REACHES_BACKEND = YES
MAX_IDENTITY_BIND_STRONG_EVIDENCE = NO

CHANNEL_OPEN_METHOD = MAX bot/deep-link handoff; tgTrack channel-open endpoint NOT observed
CHANNEL_TARGET = «Петербург 365 вход» (visible MAX Web result)
CHANNEL_OPEN_AUTOMATIC = YES from MAX deep-link flow after web handoff
EXTRA_CLICK_REQUIRED_IN_MAX_WEB = YES in this Chrome Web path
MINI_APP_TO_CHANNEL_MS = NOT_MEASURABLE; Mini App was not the observed channel opener

TGTRACK_SECOND_HALF_ARCHITECTURE = STRONG_INFERENCE
MAXIBOT_AND_TGTRACK_ARCHITECTURES_MATCH = NO / NOT_PROVEN for identity bind
```

## Observed chain

```text
mx-t.su/+MX2vpZBbmb2n3b5y
  → MAX landing/bot page
  → max://max.ru/id503501079307_1_bot?startapp=<opaque token>
  → MAX Web bot/channel handoff
  → visible channel «Петербург 365 вход»
```

В fresh tab страница MAX показала две ссылки: native `max://` и web fallback
`https://web.max.ru/id503501079307_1_bot?startapp=<same token>`. Raw token в
отчёт не записывается. Hash token из исходной страницы и hash
`WebAppStartParam` совпали.

## Mini App runtime

В MAX Web в чате «Переход в канал от Откуда Подписки» доступна отдельная кнопка
`Открыть miniapp`. После её открытия появился iframe:

```text
https://max-click.tgtrack.ru/app_bot_check.php
```

В iframe присутствует MAX WebApp payload в URL fragment. Безопасно зафиксирована
только структура:

```text
WebAppPlatform = web
WebAppVersion = 26.8.4
WebAppData keys = auth_date, chat, hash, ip, query_id, user
WebAppData present = YES
```

Raw initData, user id, IP, hash и query id не публикуются.

Значение `WebAppStartParam` в фактическом запросе:

```text
METHOD = GET
HOST = max-click.tgtrack.ru
PATH = /app_bot_check.php
QUERY_KEYS = [WebAppStartParam]
STATUS = 200
WebAppStartParam hash = e6c0596be9a09e3cf28798da67c46b76e9047bf509a148cc0ab644f0fa8a744f
equals fresh startapp token = YES
```

После загрузки `app_bot_check.php` iframe в текущем MAX Web контуре оказался на
публичной MAX promotional page. Отдельного tgTrack UI с видимым identify/bind
результатом не появилось.

## Backend identify / bind

В перехваченном runtime не обнаружены запросы с признаками:

```text
/identify
/visit
/bind
/channel-open
/redirect
```

Единственный релевантный запрос Mini App runtime — GET
`max-click.tgtrack.ru/app_bot_check.php?WebAppStartParam=<token>`.

Следовательно:

- token reaches backend: PROVEN;
- MAX WebApp payload is present in the iframe fragment: PROVEN;
- transmission of verified MAX initData to tgTrack backend: NOT_PROVEN;
- server-side bind `startapp → MAX user identity`: NOT_PROVEN;
- identity mapping sufficient for `user_id → yclid`: NOT_PROVEN.

## Сравнение с MAXIBOT

| Звено | MAXIBOT | «Откуда Подписки» | Статус сравнения |
|---|---|---|---|
| Visit-level opaque token | visit creates unique start token | fresh token in tracking URL | SAME at surface level |
| Token reaches MAX | `start_param` | `startapp` / `WebAppStartParam` | SAME |
| Verified MAX initData | validated by `/identify` | WebAppData fragment observed, backend validation not observed | DIFFERENT / NOT_PROVEN |
| Real MAX user bind | `MaxIdentityLink` + visit bind | no identify/bind request observed | NOT_PROVEN |
| Channel resolver | `/api/traffic/channel-open` | no tgTrack channel-open endpoint observed | DIFFERENT |
| Channel navigation | Mini App `window.location.replace(target_url)` | MAX deep-link/web handoff | DIFFERENT |
| Landing click | none | none in initial tracking page | SAME |
| Extra MAX Web action | wrapper-dependent | «Открыть в браузере»; Mini App separately «Открыть miniapp» | DIFFERENT |

## Ответы на обязательные вопросы

```text
DOES_TGTRACK_USE_VISIT_LEVEL_STARTAPP_TOKEN = PROVEN
DOES_TGTRACK_BIND_STARTAPP_TO_MAX_IDENTITY = NOT_PROVEN
DOES_TGTRACK_USE_MINI_APP_AS_IDENTITY_BRIDGE = NOT_PROVEN for automatic tracking path
DOES_TGTRACK_OPEN_CHANNEL_THE_SAME_WAY_AS_MAXIBOT = NO / NOT_PROVEN
DOES_TGTRACK_AVOID_EXTRA_CLICK_IN_MAX_WEB = NO in observed Chrome Web flow
```

## Что доказано и что осталось неизвестным

### PROVEN

- ссылка выдаёт opaque tracking token;
- token передаётся в MAX deep link;
- тот же token передаётся в `WebAppStartParam` запроса `app_bot_check.php`;
- у бота есть Mini App URL на `max-click.tgtrack.ru`;
- MAX Web формирует WebAppData payload с полями `user`, `chat`, `auth_date`,
  `query_id`, `hash` и `ip`;
- в наблюдаемом web flow канал открывается через MAX handoff.

### NOT_PROVEN

- backend validation of MAX initData;
- persistent `MAX user_id ↔ token/visit` bind;
- direct yclid-to-user attribution through Mini App;
- automatic channel opening performed by Mini App;
- exact server endpoint that stores the attribution.

### IMPORTANT DIFFERENCES

MAXIBOT identity bridge является доказанным backend contract:

```text
start_param → verified initData → real MAX user_id → /identify → /channel-open
```

У «Откуда Подписки» в этом тесте доказана только первая половина:

```text
startapp → WebAppStartParam → app_bot_check.php
```

Вторая половина не наблюдалась. Наличие WebAppData в fragment само по себе не
доказывает серверную атрибуцию.

## USEFUL_CHANGES_FOR_MAXIBOT

Из этого black-box теста не следует необходимость менять MAXIBOT. Полезное
подтверждение уже совпадает с текущей архитектурой MAXIBOT: opaque token можно
передавать через MAX `startapp`. Но MAXIBOT дополнительно и доказанно выполняет
verified identity bind и channel resolver; отказываться от них нельзя на основе
одного внешнего Mini App.

Чужой канал не открывался через «Вступить», JOIN_REQUEST не создавался, backend
TeleGraphyx/tgTrack не запрашивался и не исследовался.
