# Handoff: automatic Yandex delivery for EXACT subscriptions

Дата: 2026-08-25

## Статус

IMPLEMENTATION = PASS
PRODUCTION_DEPLOY = PASS
AUTO_EXACT_SEND = ENABLED
ATTRIBUTION_CHANGED = NO
MAX_FUNNEL_CHANGED = NO

## Что сделано

- После официального `user_added` и однозначного `MaxIdentityLink` создаётся
  `userDidSubscribe` conversion queue row.
- После commit эта EXACT-запись передаётся штатному Yandex sender автоматически.
- При успешной отправке запись получает `sent` и `sent_at`.
- При ошибке отправки запись остаётся `pending`; ошибка сохраняется только как
  безопасная категория исключения для повторной обработки.
- `UNATTRIBUTED`, `HIGH`, `MEDIUM` и `AMBIGUOUS` в sender не допускаются.
- Идемпотентность сохранена: повторный `user_added` не создаёт дубль queue row.
- Старые `dry_run` записи автоматически не переотправляются.

## Файлы

- `app/bot/menu.py`
- `tests/test_max_channel_sync.py`

Миграции: NONE.
Новые env-переменные: NONE.
Production env: `YANDEX_CONVERSION_DRY_RUN=false`.

## Проверки

- Production container `py_compile`: PASS.
- Targeted tests в production API container: `13 passed`.
- API health: PASS.
- API `MAX_RUN_POLLING=false`: PASS.
- Sole worker `MAX_RUN_POLLING=true`: PASS.
- Worker не пересоздавался.

## Controlled result

Перед включением автоматического режима были отправлены ровно 5 свежих
подтверждённых EXACT-конверсий; все получили Yandex upload status `UPLOADED`.
Неподтверждённые записи не затронуты.

После первого deploy была обнаружена эксплуатационная рассинхронизация: API
уже содержал auto-sender, а worker продолжал работать на старом image. Worker
пересобран и перезапущен штатно; новая заявка владельца после этого получила
один controlled retry и статус `UPLOADED`. Текущий API и worker содержат одну
и ту же auto-sender реализацию.

## Остаточные ограничения

- `UPLOADED` означает принятие загрузки API; итоговая привязка к визиту в
  интерфейсе Метрики может появляться с задержкой.
- Автоматический sender не обещает отправку для заявки без полной цепочки
  `yclid → visit → verified MAX identity → EXACT`.
