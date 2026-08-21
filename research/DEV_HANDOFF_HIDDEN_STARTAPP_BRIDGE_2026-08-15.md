# Dev handoff: hidden MAX startapp bridge

Дата: 2026-08-15

## Целевая схема

```text
Yandex Direct
  → landing (save yclid/UTM/click visit)
  → automatic 307 to MAX bot startapp
  → silent traffic Mini App
  → verified MAX initData binds MAX user_id to visit
  → channel-open endpoint validates channel-scoped target
  → automatic navigation to closed MAX channel
  → join request / manual approval
  → confirmed membership
  → conservative userDidSubscribe
```

Пользователь не видит чат или меню MAXIBOT и не нажимает кнопку на landing или в bridge. Mini App может быть виден как системный MAX webview на короткое время; этот системный слой нельзя полностью скрыть приложением.

## Что изменено

- `app/main.py`: при включённом `TRAFFIC_BOT_ATTRIBUTION_ENABLED` landing сразу вызывает `_traffic_redirect(..., via_bot=True)`.
- `app/main.py`: direct native fallback остаётся только для окружений с отключённым bridge; exact attribution в таком режиме не заявляется.
- `app/main.py`: bridge fail-closed, если отсутствует `MAX_MINIAPP_URL` или `MAX_BOT_USERNAME`.
- `miniapp/traffic.html`: удалены видимые заголовок, кнопки «Открыть канал» и «Проверить подписку»; flow автоматически выполняет `identify → channel-open → location.replace(target_url)`.
- `tests/test_landing_ui_contract.py`: landing contract обновлён на silent startapp redirect.
- `tests/test_traffic_deep_link.py`: добавлен silent Mini App contract.

Миграции: нет. Модели `TrafficVisit`, `MaxIdentityLink`, `TrafficEvent` уже существовали и используются без изменения схемы.

## Env

Для production API необходимы существующие переменные:

- `TRAFFIC_BOT_ATTRIBUTION_ENABLED=true`;
- `MAX_BOT_USERNAME` — username бота, без значения в отчёте;
- `MAX_MINIAPP_URL` — настроенный URL Mini App, без значения в отчёте;
- `MAX_BOT_TOKEN`, `APP_SECRET`, `DATABASE_URL` — существующие секретные переменные.

В `docker-compose.vps.yml` API уже задаёт `TRAFFIC_BOT_ATTRIBUTION_ENABLED=true`. Polling policy не менялась: только VPS worker имеет `MAX_RUN_POLLING=true`.

## Проверки

- Python syntax compile изменённых Python-файлов: PASS.
- Static silent Mini App contract через Node: PASS.
- Полный pytest: BLOCKED — в доступном Python 3.11 отсутствует pytest; системный Python несовместим с проектным синтаксисом.
- Docker QA build: BLOCKED — registry TLS timeout при получении `python:3.12-slim`.
- Реальный MAX/iOS E2E: BLOCKED — нет owner-controlled MAX test session в текущем workspace. Поэтому автоматическое открытие native channel и свежий iOS proof не объявляются PROVEN.

## Безопасность и атрибуция

- В startapp передаётся только opaque token.
- `yclid`, UTM и PII в MAX URL не передаются.
- `MAX user_id` принимается только из verified MAX initData.
- `channel-open` получает target только из `TrafficProfile` этого landing/channel.
- При отсутствии target или bridge конфигурации редирект блокируется.
- `userDidSubscribe` не менялся и не должен отправляться без доказанной связки visit → MAX user → confirmed membership.

## Статус

| Пункт | Статус |
|---|---|
| Landing без промежуточного preview/click | PASS по коду и контрактам |
| Silent startapp bridge | PASS по коду; runtime E2E BLOCKED |
| Каналы не смешиваются | PASS: target scoped by landing channel |
| Manual approval path | Не изменён; остаётся обязательным |
| Auto-approve | Не изменён и не доказан |
| iPhone MAX Web/native E2E | BLOCKED без свежего теста |
| Production deploy | Не выполнялся |
