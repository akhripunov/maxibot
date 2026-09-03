# MAXIBOT: обязательное правило пользовательского пути

Пользователь на лендинге ничего не нажимает.

После открытия лендинга система автоматически выполняет редирект в MAX по
нужному tracking/deep link. Кнопка «Перейти» на лендинге не является частью
пользовательского сценария и не должна требоваться для перехода.

Правильный путь:

```text
Яндекс Директ → лендинг → автоматический редирект → MAXIBOT/канал → вступление
```

Не добавлять промежуточные кнопки, подтверждения или действия пользователя на
лендинге, если это отдельно не запрошено архитектором.

# Роли и порядок работы

## Архитектор

Архитектор формулирует целевую схему, принимает архитектурные решения,
утверждает ограничения и выдаёт dev единое ТЗ. Архитектор не считает задачу
готовой по наличию кода — требуется проверяемый handoff.

## Dev

Dev изучает текущий код, реализует утверждённое ТЗ, не меняет согласованную
архитектуру самовольно, запускает тесты и проверяет результат на staging/VPS.
После завершения dev обязан передать архитектору handoff:

- что сделано;
- какие файлы и миграции изменены;
- команды запуска и переменные окружения;
- результаты тестов;
- результаты реального E2E/VPS-теста;
- статусы PASS/BLOCKED;
- точный оставшийся блокер, если он есть.

# Главная рабочая схема

```text
Яндекс Директ
  → landing
  → автоматический redirect без клика на landing
  → MAXIBOT/tracking deep link
  → нужный подключённый MAX-канал
  → вступление или заявка
  → ручное одобрение владельцем
  → подтверждённое членство
  → userDidSubscribe в Яндекс Метрику
```

## Получение ссылки подключённого канала

Для подключённого MAX-канала не просить владельца копировать invite URL.

```text
bot_added
  → получить chat_id
  → GET /chats/{chat_id} через официальный MAX Bot API
  → взять response.link
  → сохранить link как target/invite_url этого chat_id
```

Нельзя смешивать `chat_id`, владельцев или ссылки разных каналов. Если MAX не
вернул непустой валидный `link`, редирект блокируется и в handoff указывается
точный статус BLOCKED.

## Связка MAX-пользователя с yclid

Для точной атрибуции не считать прямой переход по общей ссылке канала
достаточным. Использовать tracking deep link бота:

```text
landing получает yclid/UTM
  → создаёт opaque click_id
  → автоматический redirect на MAXIBOT?start=payload
  → bot_started даёт MAX user_id и payload/click_id
  → MAXIBOT отправляет этому пользователю link нужного канала
  → user_added фиксирует user_id + channel chat_id
  → user_id → click_id → yclid
  → userDidSubscribe после подтверждённого членства
```

Пользователь не вводит ссылку и не нажимает кнопку на landing. Для закрытого
канала ручное одобрение владельцем остаётся обязательным, пока отдельно не
доказан production auto-approve.

Пользователь на landing ничего не нажимает. Любая кнопка на landing,
требующая действия для перехода, считается нарушением утверждённого UX.

# Структура проекта

- `app/` — основное приложение, API, bot runtime, интеграции и бизнес-логика.
- `app/bot/` — меню, webhook/event handling и пользовательские сценарии бота.
- `app/integrations/` — клиенты MAX, Яндекс и внешние интеграции.
- `miniapp/` — интерфейс мини-приложения и traffic/landing UI.
- `demo/` — демонстрационные страницы и локальный UI-прототип.
- `tests/` — unit, integration и contract-тесты.
- `alembic/` — миграции базы данных.
- `deploy/` и `docker-compose*.yml` — deployment и VPS-конфигурация.
- `research/` — отчёты исследований, доказательства и handoff-материалы.
- `App Design/` — согласованные визуальные макеты.

# Критические ограничения

- Не использовать user-level WebSocket и opcode 55/77 без отдельного
  утверждённого безопасного ТЗ.
- Не отправлять `userDidSubscribe`, если нет доказанной связи подписчика с
  рекламным визитом.
- Не подставлять неизвестный или чужой канал, если target/invite URL не
  получен.
- Не выводить токены, cookies, raw frames и персональные данные в логи.
- Не объявлять E2E/production готовыми только по unit-тестам.

## Эксплуатационное правило: MAX updates читаются в одном месте

- Production MAX updates читает только выделенный VPS `worker`.
- Единственный production host для polling: `135.106.166.46`.
- Legacy VPS `37.252.10.67` не должен запускать
  `maxibot-vps-worker-1`; контейнер обязан оставаться остановленным с
  restart policy `no`. После любого deploy проверять оба VPS, а не только
  локальный Docker и новый production host.
- VPS `api` и локальный `docker-compose` обязаны иметь
  `MAX_RUN_POLLING=false`; единственный VPS worker явно имеет `true`.
- Нельзя запускать локальный MAXIBOT с production MAX token и polling.
- Перед диагностикой дублей меню сначала проверять наличие второго poller-а;
  не удалять пользовательские сообщения и не менять модели каналов как обход.
- Подробная процедура закреплена в
  `research/MAXIBOT_SINGLE_POLLER_RUNBOOK.md`.

## Зафиксированный production-контракт mobile MAX handoff

Рабочий iPhone-путь подтверждён двумя реальными записями владельца 2026-08-21:

```text
mobile landing
  → max://max.ru/<bot>?startapp=<same opaque token>
  → MAX Mini App
  → POST /api/traffic/resolve
  → iPhone: обычный HTTPS channel invite через programmatic anchor target=_blank
  → нужный MAX-канал
```

Этот контракт нельзя менять как косметический рефакторинг:

- iPhone/iPad Mini App не переводить на безусловный `WebApp.openMaxLink()` —
  реальный iOS-клиент молча оставался на экране «Открываем канал…»;
- Android сохраняет `window.location.replace(target_url)`;
- MAX Web/desktop может использовать `WebApp.openMaxLink(target_url)` только
  при исключённом Apple mobile user agent;
- один и тот же `startapp` token обязан сохраняться от landing до Mini App;
- нельзя добавлять новый visit/token, timer-navigation, iframe или обязательный
  пользовательский клик;
- любые изменения `app/main.py`, `app/traffic/deep_link.py` или
  `miniapp/traffic.html`, затрагивающие handoff, требуют прохождения
  `tests/regression/test_mobile_max_handoff_contract.py` и двух свежих ручных
  smoke на настоящем iPhone: (1) ссылка из MAX, (2) ссылка из Safari;
- пока оба real-device smoke не завершились переходом в целевой канал, статус
  релиза `BLOCKED`, а предыдущий production handoff должен быть восстановлен.

# Canonical Codex orchestration

- `ARCHITECT CODEX` — единственный общий технический orchestrator MAXIBOT.
- Рабочие роли проекта: `DEV`, `WEBDEV`, `ДИРЕКТОЛОГ`, `QA / ТЕСТИРОВЩИК`.
  Старые Codex tasks/chats не считаются дополнительными ролями.
- `ARCHITECT CODEX` выбирает minimum sufficient team, принимает финальное
  техническое решение и после handoff проводит integration review.
- `DEV` отвечает за backend/bot/DB/API/workers, `WEBDEV` — за сайт и frontend,
  `ДИРЕКТОЛОГ` — consultative по Direct/Metrika/attribution, `QA` — независимый
  release gate. Specialist roles не принимают финальные архитектурные решения.
- `Ads Codex Worker` — отдельный bounded runtime-контур рекламной аналитики,
  не общий development orchestrator.
- Для подробного ROLE REGISTRY и routing использовать
  [`docs/ROLE_REGISTRY.md`](docs/ROLE_REGISTRY.md) и
  [`docs/ORCHESTRATION_PROTOCOL.md`](docs/ORCHESTRATION_PROTOCOL.md).
- Перед существенной задачей обязателен repository preflight; `UNKNOWN` и
  `UNVERIFIED` нельзя выдавать за `PASS`.
