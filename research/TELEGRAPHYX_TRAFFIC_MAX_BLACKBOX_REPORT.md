# Black-box: TeleGraphyx Traffic → MAX

Дата: 2026-08-11. Проверены только публичные страницы и публично загруженный frontend-код; авторизация в чужой/непредоставленный кабинет и обход защиты не выполнялись.

## Executive verdict

**D — обнаружен альтернативный механизм: TeleGraphyx ведёт посетителя в собственного MAX-бота через `startapp`, а не напрямую на native invite канала.**

Точная серверная запись `visitor → subscription` по публичным данным не доказана, потому что для неё нужен авторизованный тестовый проект TeleGraphyx и реальное вступление тестового пользователя. Но публичный маршрут уже исключает гипотезу «каждому посетителю выдаётся отдельный native MAX invite» для проверенного лендинга.

## Visitor A / Visitor B

Проверенный публичный лендинг из собственного материала TeleGraphyx:

```text
https://max.tgryx.ru/69092437303297
```

| Посетитель | TeleGraphyx URL | Конечный MAX URL |
|---|---|---|
| A | `/69092437303297` | `https://max.ru/id510107251374_bot?startapp=client_38393234313936` |
| B | `/69092437303297` | `https://max.ru/id510107251374_bot?startapp=client_38393234313936` |

Результат: конечные URL **одинаковые**. В URL нет `join/<native-invite-token>`; есть deep-link к боту `id510107251374_bot` и `startapp`.

Значение `38393234313936` при hex-декодировании даёт `8924196`, что согласуется с идентификатором объекта/лендинга, а не с уникальным browser visitor. Это гипотеза о формате, не доказательство серверной схемы.

## Redirect / deep-link chain

Наблюдаемый логический маршрут:

```text
TeleGraphyx landing
  ↓
https://max.ru/id510107251374_bot?startapp=client_38393234313936
  ↓
MAX Web page for TeleGraphyx bot
  ├─ max://max.ru/id510107251374_bot?startapp=client_38393234313936
  └─ https://web.max.ru/id510107251374_bot?startapp=client_38393234313936
```

MAX Web сохраняет `startapp` в ссылке «Открыть в браузере». В проверенном маршруте параметры `utm_*`, `yclid`, `visitor_id` и `click_id` на конечной странице не наблюдались.

Публичный пример TeleGraphyx описывает ту же воронку как «лендинг → переход в MAX → пост с кнопкой подписки», а подключение канала и проверку подписки выполняет бот TeleGraphyx.

## Что найдено в публичном frontend кабинета

Загруженный bundle `https://lk.telegraphyx.ru/assets/index-KIB8lYmJ.js` содержит следующие вызовы:

```text
POST /api/auth/start-login
GET  /api/auth/check-login?token=...
POST /api/max/auth/magic-login

GET  /api/max/channels
GET  /api/max/dashboard/summary?channelUUID=...
GET  /api/max/dashboard/timeseries?channelUUID=...
GET  /api/max/dashboard/top-sources?channelUUID=...&period=...&limit=...
GET  /api/max/subscription/{id}
GET  /api/max/ads-report/...
```

В bundle есть классификатор источников:

```js
{
  yandex_direct: "Я.Директ",
  invite_link: "Ссылка",
  unknown: "Без источника"
}
```

Это доказывает наличие backend-агрегации подписок по источнику, но не раскрывает, как конкретная подписка сопоставляется с конкретным visitor: в публичном JS нет видимых полей `visitor_id`, `click_id`, `landing_id`, `max_user_id` или `yclid`.

## Объекты visitor/subscription

Публично получить их не удалось:

```text
visitor object: не доступен без авторизации и собственного проекта
subscription object: не доступен без авторизации и собственного проекта
```

Найденные API-имена показывают наличие объекта/раздела подписок и статистики, но не дают права вызывать приватные endpoints без сессии владельца.

## Точка точной атрибуции

На текущем black-box этапе точная точка не доказана. Наиболее вероятная архитектура:

```text
landing_id/client_id
  → startapp payload в MAX-боте
  → bot_started/startapp callback с user_id
  → TeleGraphyx сохраняет связку user_id + client/landing
  → бот или backend проверяет членство пользователя в канале
  → subscription/conversion записывается для этого источника
```

Ключевое различие с native invite: источник, вероятно, фиксируется на уровне бота до или во время проверки подписки, а не приходит из события MAX `user_added`.

## Воспроизводимость в MAXIBOT

**PARTIAL.** MAXIBOT уже умеет получать `bot_started` и сохранять payload `/start`; аналогичный контролируемый deep-link к боту можно реализовать. Однако доказать факт подписки на канал и серверную связку `user_id → channel membership` можно только при наличии соответствующих прав и тестового канала. Перенести саму TeleGraphyx серверную логику без доступа к её backend нельзя.

## Что нужно для окончательного доказательства

Нужен собственный авторизованный TeleGraphyx тестовый проект:

1. создать/получить два лендинга или две кампании;
2. открыть каждый из двух чистых браузерных профилей;
3. зафиксировать разные `startapp` payload, если они выдаются;
4. начать MAX-бота двумя тестовыми аккаунтами;
5. подписать аккаунты на один тестовый канал;
6. сопоставить `bot_started`, проверку членства и ответ `/api/max/dashboard/top-sources`/подписки;
7. только при наличии явного ID в ответе считать цепочку доказанной.

