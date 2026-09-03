# Browser handoff diagnostics — 2026-08-22

## Что сделано

Добавлена только best-effort telemetry для traffic landing и Mini App. Пользовательский маршрут, token, MAX Mini App handoff, attribution и Яндекс не изменены.

Новые безопасные стадии одного визита:

1. `JS_LANDING_STARTED` — браузер выполнил inline JavaScript landing.
2. `MAX_LINK_ASSIGNED` — браузер дошёл до строки назначения native MAX handoff.
3. `PAGE_HIDDEN` — landing ушёл в background после handoff.
4. `PAGE_RESTORED` — landing снова стал видимым после возврата.
5. `MINIAPP_CHANNEL_HANDOFF_STARTED` — Mini App действительно начала открытие уже разрешённого target URL.
6. `MINIAPP_PAGE_HIDDEN` — Mini App ушло в background после начала handoff.
7. `MINIAPP_PAGE_RESTORED` — Mini App снова стала видимой после возврата.

Landing-события отправляются `navigator.sendBeacon`; Mini App отправляет best-effort `keepalive` request. Оба пути обёрнуты в `try/catch`: ошибки telemetry не могут остановить handoff. Нет timer, iframe, fallback redirect или нового visit/token. Существующие platform branches сохранены: iPhone/iPad — programmatic anchor, Android — `window.location.replace`, MAX Web/desktop — bridge.

## Изменённые файлы

- `app/main.py`
- `miniapp/traffic.html`
- `tests/test_landing_ui_contract.py`
- `tests/regression/test_confirmed_max_handoff.py`
- `tests/regression/test_traffic_diagnostic_stages.py`

## Миграции и env

- Миграций нет.
- Новых env-переменных нет.

## Тесты

В candidate API container на production VPS:

```text
tests/test_landing_ui_contract.py
tests/regression/test_confirmed_max_handoff.py
tests/regression/test_traffic_diagnostic_stages.py
tests/regression/test_mobile_max_handoff_contract.py
```

Результат: `28 passed`.

## Production

- Host: `135.106.166.46`.
- Только `api` пересоздан штатно через `docker-compose.vps.yml`.
- `API_HEALTH_READY = PASS` (internal and public HTTPS).
- `API_POLLING = false`.
- `WORKER_POLLING = true`.
- Единственный worker сохранён; worker/DB/Redis не пересоздавались.
- Rollback copies: `/opt/maxibot/app/main.py.pre-miniapp-stage-telemetry-20260822`, `/opt/maxibot/miniapp/traffic.html.pre-miniapp-stage-telemetry-20260822`.

## Использование диагностики

Для нового рекламного визита:

- нет `JS_LANDING_STARTED` — HTML ответ получен сервером, но landing JS не доказан; возможны content filter/whitelist/network/browser execution;
- есть `JS_LANDING_STARTED`, нет `MAX_LINK_ASSIGNED` — ошибка landing JavaScript до handoff;
- есть `MAX_LINK_ASSIGNED`, нет `PAGE_HIDDEN` и нет `/mini` — браузер не передал deep link MAX;
- есть `/mini`/`HANDOFF_CONFIRMED` — MAX Mini App действительно открылся;
- есть `MINIAPP_CHANNEL_HANDOFF_STARTED` — Mini App начала handoff в канал; это сильнее server-side `channel_open_clicked`, который фиксируется до исполнения browser navigation;
- есть `MINIAPP_PAGE_HIDDEN` — WebView скрылся после handoff; это не является доказательством заявки или membership;
- есть `MINIAPP_PAGE_RESTORED` без `user_added` — пользователь вернулся в Mini App без подтверждённого вступления; нужно сопоставлять с экраном MAX и дальнейшей заявкой;
- `HANDOFF_CONFIRMED` не является доказательством вступления: заявки и membership проверяются отдельно.

## Статус

- `BROWSER_STAGE_DIAGNOSTICS = PASS`
- `MINIAPP_HANDOFF_STAGE_DIAGNOSTICS = PASS`
- `PRODUCTION_DEPLOY = PASS`
- `REAL_IOS_SMOKE_AFTER_CODE_CHANGE = BLOCKED` — telemetry не меняет navigation branch, но свежий ручной iPhone smoke всё ещё требуется по release contract для подтверждения общего mobile handoff.
