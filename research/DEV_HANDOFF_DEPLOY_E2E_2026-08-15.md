# Dev handoff: production deploy, single poller и MAX E2E

Дата: 2026-08-15

## 1. Что сделано

- Найден и использован штатный production target: VPS `135.106.166.46`, каталог `/opt/maxibot`.
- Hidden startapp bridge и актуальная traffic-логика доставлены в production-образ.
- Устранён двойной ответ на один логический запуск: пара `bot_started(payload)` + `message_created(/start)` теперь имеет общий краткий user-level guard; exact retry guard увеличен до 30 секунд.
- Подтверждено, что локальный `maxibot-api-1` остановлен и production MAX updates читает только VPS worker.
- После пользовательского E2E найден второй production poller на legacy VPS
  `37.252.10.67`: `maxibot-vps-worker-1` работал с polling=true. Он остановлен,
  restart policy установлен в `no`; единственный активный worker остался на
  `135.106.166.46`.
- Исправлена навигация callback: обычные экраны меняются атомарно через официальный `POST /answers`, без конкурирующих `DELETE + PUT`.
- Исправлено «Настройка сообщений»: удалено недопустимое image attachment с `payload.url`; MAX требует upload token. Для settings используется безопасная новая service-панель без удаления сообщений, потому что MAX Web скрывает in-place replacement этого экрана даже при HTTP 200.
- `/admin` проверен реальным сообщением в production MAX.
- Пользовательские сообщения не удалялись. Перезапуск не использовался как диагностический обход; контейнеры пересоздавались только как штатная часть deploy нового образа.

## 2. Изменённые файлы

- `app/bot/runtime.py`
- `app/bot/menu.py`
- `tests/test_runtime_entrypoints.py`
- `app/main.py` (hidden bridge, ранее подготовлен)
- `app/traffic/deep_link.py` (hidden bridge, ранее подготовлен)
- `miniapp/traffic.html` (hidden bridge, ранее подготовлен)
- `research/DEV_HANDOFF_DEPLOY_E2E_2026-08-15.md`

Временный локальный `Dockerfile.hotfix` после deploy удалён. На VPS он оставлен только как воспроизводимый минимальный build-файл текущего hotfix-образа.

## 3. Миграции

Новых миграций нет.

## 4. Env без значений секретов

- `MAX_BOT_TOKEN`
- `MAX_BOT_USERNAME`
- `MAX_RUN_POLLING`
- `MAX_UPDATES_MODE`
- `TRAFFIC_BOT_ATTRIBUTION_ENABLED`
- `MAX_MINIAPP_URL`
- `APP_SECRET`
- `DATABASE_URL`
- `REDIS_URL`
- Yandex OAuth/Метрика env из существующей production-конфигурации

Production policy доказана после последнего deploy:

```text
api    MAX_RUN_POLLING=false
worker MAX_RUN_POLLING=true
```

## 5. Тесты

- `py_compile app/bot/runtime.py app/bot/menu.py tests/test_runtime_entrypoints.py` — PASS.
- Production container: `pytest -q tests/test_runtime_entrypoints.py -k duplicate_logical_start` — `1 passed`.
- Production runtime probe: paired `bot_started(payload)` then `/start` is claimed once; callback keyboard conversion is valid — PASS.
- Добавлены regression-проверки:
  - paired `bot_started + /start` is one logical launch;
  - navigation callback uses atomic `/answers` and does not DELETE/PUT;
  - settings screen does not send an unuploaded image URL;
  - `/admin` route and settings route remain covered.

Ограничение: hotfix-образ наследует старую test tree, поэтому новые статические tests не все входят внутрь production image; новая runtime-логика дополнительно проверена production probe и реальным Chrome E2E.

## 6. VPS / реальный E2E

- VPS API healthy после финального deploy — PASS.
- API polling `false`, worker polling `true` — PASS.
- Один MAXIBOT polling container (`python -m app.poller`) — PASS.
- Legacy worker: stopped, restart policy `no` — PASS.
- Локальный MAXIBOT API/poller остановлен — PASS.
- Chrome, авторизованный MAX Web:
  - одно сообщение `/start` добавлено один раз;
  - production log: один `message_created`, один `MAX start command handled`, один `POST /messages 200`, один `MAX start menu completed`;
  - второго ответа нет — PASS;
  - после устранения legacy poller повторный `/start` в 17:46 показал одну
    новую панель; production log содержит один update и один POST — PASS;
  - `Мои каналы → Москва 365 → Настройка сообщений пользователя` открывает видимую панель с кнопками «Приветственное сообщение» и «Уведомление об отправке» — PASS;
  - `/admin` показывает `MAXIBOT · Администратор` — PASS.
- `gptgirls-*` не затрагивались.
- Токены/cookies/raw frames в handoff не включены.

Остаточная эксплуатационная проблема: SSH к `135.106.166.46:22` нестабилен. Наблюдались `banner exchange timeout` и закрытие соединения до server banner. Устойчивую сессию удалось получить через SSH multiplexing. Это сетевой блокер Selectel, не ошибка MAXIBOT.

Отдельно: один подключённый пользовательский custom bot возвращает `401 Invalid access_token`. Основной MAXIBOT token и polling работают (`GET /updates 200`). Невалидный custom-bot token не является вторым production poller и не вызывает дубль `/start`; владельцу нужно обновить токен этого подключённого бота.

## 7. Исследование attribution-сервисов

Полный black-box handoff: `research/ATTRIBUTION_SERVICES_MAX_BLACKBOX_HANDOFF.md`.

| Сервис | Наблюдаемая/заявленная механика | A/B/C | Итог |
|---|---|---:|---|
| TeleGraphyx | Публичный MAX bot/startapp flow наблюдался; unique native invite заявлен, exact `visitor → MAX user → member` не доказан | не завершены | NOT_PROVEN |
| tgTrack / «Откуда Подписки» | tracking script + прямой переход в MAX + последующее сопоставление заявлены; внутренний matching не раскрыт | не завершены | BLOCKED |
| Linker | фактическая MAX-цепочка не извлечена | не завершены | BLOCKED |
| Gatekeeper | фактическая MAX-цепочка не извлечена | не завершены | BLOCKED |
| Mobzio | фактическая MAX-цепочка не извлечена | не завершены | BLOCKED |
| BotHelp | фактическая MAX-цепочка не извлечена | не завершены | BLOCKED |
| Quescha | фактическая MAX-цепочка не извлечена | не завершены | BLOCKED |

## 8. PROVEN / CLAIMED / NOT_PROVEN / BLOCKED

PROVEN:

- один production poller на VPS;
- один ответ MAXIBOT на один проверочный `/start`;
- рабочие `/admin` и «Настройка сообщений» в MAX Web;
- landing/traffic bridge находится в production image;
- MAX API принимает атомарные navigation callbacks через `/answers`.

CLAIMED:

- competitor exact attribution и заявленная точность;
- unique-per-visitor invite у TeleGraphyx;
- внутренний source matching tgTrack.

NOT_PROVEN:

- полный production funnel `yclid → MAX user_id → join request → approved member → userDidSubscribe`;
- свежий iPhone native/MAX Web E2E;
- production auto-approve.

BLOCKED:

- обязательные независимые A/B/C-сессии для семи competitor services;
- сетевой SSH banner issue у VPS provider;
- custom bot с отозванным/невалидным владельческим token до его обновления.

## 9. Применимость механизма к MAXIBOT

Hidden startapp identity bridge применим и развёрнут как согласованный способ связать traffic token с MAX user без промежуточного меню MAXIBOT. Отправлять `userDidSubscribe` разрешено только после фактического membership event и доказанной visit attribution. Competitor unique-invite/matching механики пока нельзя переносить как доказанные.

## 10. Итоговые статусы

| Пункт | Статус |
|---|---|
| Production deploy на `135.106.166.46` | PASS |
| API health | PASS |
| Единственный VPS poller | PASS |
| Локальный production poller отсутствует | PASS |
| Дубль одного `/start` устранён | PASS |
| `/admin` | PASS |
| «Настройка сообщений» | PASS |
| Hidden startapp bridge в production image | PASS |
| Полный attribution → approved member → Yandex E2E | BLOCKED |
| iPhone native/MAX Web E2E | BLOCKED |
| Competitor A/B/C black-box | BLOCKED |
| Стабильный SSH/TCP22 | BLOCKED |

## 11. Свежая проверка цепочки продвижения (Chrome, 2026-08-15)

Проверен реальный production-путь: `Продвижение → Москва 365 → персональная ссылка`.

| Этап | Результат |
|---|---|
| Персональная ссылка открывается | PASS |
| Landing выполняет автоматический переход без клика | PASS |
| В MAX формируется opaque `startapp` deep link | PASS |
| MAX Web показывает системный экран запуска | OBSERVED |
| Проверенный `bot_started/initData` и связка с MAX user_id | NOT_PROVEN |
| Автоматическое открытие именно закрытого канала | BLOCKED |
| Join request/member | BLOCKED |
| `userDidSubscribe` в Яндекс Метрику | BLOCKED |

В текущей Chrome-сессии MAX Web после системного экрана вернулся в уже существующий диалог; свежий CTA закрытого канала и membership event не наблюдались. Поэтому полный production E2E не объявляется готовым. Токены, cookies и raw payload в отчёт не включены.

## 12. Найденный фикс после проверки

Причина: маршрут `GET /mini` уже существовал и отдавал обычный `miniapp/index.html`, тогда как `startapp` должен открывать скрытый `miniapp/traffic.html`. Нижестоящий дублирующий маршрут не мог изменить результат из-за первого совпадения маршрута.

Исправлено локально: исходный `/mini` теперь отдаёт `traffic.html`; добавлен regression-тест. Локальная проверка: `17 passed`.

Production deploy этого последнего изменения пока BLOCKED из-за повторяющегося закрытия SSH до banner exchange на `135.106.166.46`. Предыдущий bridgefix-образ уже был собран и применён, но он содержит старую реализацию `/mini`; поэтому финальный E2E после найденного фикса ещё не заявляется.

После настройки Mini App в кабинете MAX повторно проверен публичный `https://go.maxibot.jobjin.ru/mini`: он всё ещё отдаёт старый `index.html`, а не silent `traffic.html`. Значит, финальный фикс `/mini` пока не попал в production image/public route; full E2E остаётся BLOCKED.

Повторный deploy выполнен после этого наблюдения: API и worker работают на `maxibot-vps-api:20260815-bridgefix`; Chrome подтверждает, что `/mini` теперь отдаёт пустой silent bridge с заголовком «Переход в канал» — PASS. В MAX Web открытие Mini App через существующую кнопку не передало `start_param` (`hasStartParam=false`), поэтому `identify → target channel` в этой Chrome Web-сессии не стартовал; full attribution E2E остаётся BLOCKED до свежего native MAX/iOS или корректного Web startapp теста.

После решения убрать Mini App из основного пути применён direct-flow deploy `maxibot-vps-api:20260815-directflow`. Свежий Chrome E2E: `Продвижение → Москва 365 → персональная ссылка → https://max.ru/join/...` показывает экран именно канала «Москва 365», затем «Открыть в браузере» открывает канал; чат MAXIBOT в цепочке отсутствует — PASS. Membership и `userDidSubscribe` не заявлены: прямая общая ссылка не доказывает `yclid → MAX user_id`.

По обновлённому `AGENTS.md` direct-flow признан неверным для целевой атрибуции и отменён. Production возвращён на `maxibot-vps-api:20260816-trackingflow`: landing снова создаёт tracking deep link `?startapp=<opaque payload>`; свежий Chrome переход из «Продвижение» подтверждает именно URL MAXIBOT, а не общий channel join — PASS. Полная связка `bot_started → user_id → channel membership → userDidSubscribe` пока не заявлена PASS и требует доказательного теста MAX event path.
