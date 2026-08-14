# MAXIBOT Traffic PoC через unique startapp

Реализованный контур:

```text
/traffic-test
  → opaque visitor/startapp token
  → https://max.ru/<MAXIBOT_USERNAME>?startapp=<token>
  → verified MAX bot_started или verified WebApp initData
  → visitor ↔ MAX user
  → GET /chats/{chatId}/members?user_ids=<MAX user>
  → traffic_subscriptions(status=active)
```

## Endpoints

- `GET /traffic-test?landing=<public_id>` — PoC landing. Если активный landing ровно один, `landing` можно не указывать.
- `GET /traffic-test/click` — выдаёт unique MAX bot deep-link.
- `POST /api/traffic/identify` — валидирует `X-MAX-Init-Data` и связывает startapp token с MAX user.
- `POST /api/traffic/channel-open` — idempotently записывает открытие целевого канала.
- `POST /api/traffic/check-subscription` — проверяет membership конкретного user ID и создаёт idempotent subscription.
- `GET /traffic-test/app` — минимальный WebApp UI для identify/open/check.
- `GET /admin/traffic-debug` — read-only диагностика для администратора MAXIBOT.

## Безопасность

- startapp token генерируется через `secrets.token_urlsafe` и не содержит UTM/YCLID/PII.
- `max_user_id` не принимается из body: он берётся только из проверенного MAX initData или server-authenticated MAX update.
- raw initData не хранится; сохраняется SHA-256 fingerprint для аудита.
- identity link запрещает повторно привязать одного visitor к другому MAX user.
- события и subscription имеют dedupe/unique constraints.
- bot token и raw payload не пишутся в обычные application logs.

## Membership API

Используется официальный `GET /chats/{chatId}/members` с параметром `user_ids`; MAX требует, чтобы бот был администратором канала. Один user проверяется без загрузки полного списка участников.

## Что ещё требуется для полного YES

Нужен реальный тестовый канал, бот-администратор и два тестовых MAX-аккаунта. В текущем окружении выполнена реализация и статическая проверка, но live-эксперимент с двумя аккаунтами и production MAX API не запускался.
