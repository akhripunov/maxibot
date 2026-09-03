# Handoff: safe Landing → MAX → Mini App telemetry

Дата: 2026-08-25

## Статус

IMPLEMENTATION = PASS
PRODUCTION_DEPLOY = PASS
PRODUCTION_CHANGED = YES (API only)
ATTRIBUTION_CHANGED = NO
YANDEX_SENDER_CHANGED = NO
USER_DID_SUBSCRIBE_CHANGED = NO

Изменение ограничено диагностической телеметрией. Бизнес-путь и locked mobile
handoff branch не менялись: не добавлялись новый visit/token, timer-navigation,
iframe, fallback-ветка или обязательный клик.

## Что сделано

- Добавлен allowlist безопасных telemetry event types для landing, Mini App,
  bind/channel-open и client errors.
- Landing отправляет best-effort стадии `JS_LANDING_STARTED`,
  `MAX_LINK_ASSIGNED`, `MAX_REDIRECT_ATTEMPTED`, visibility/page lifecycle,
  fallback-click и sanitized `CLIENT_ERROR`.
- Mini App отправляет pre-init сигналы как `trusted=false`; после валидной
  MAX initData сервер помечает события `trusted=true`. Эти события не меняют
  identity/attribution/conversion state.
- Backend фиксирует server-confirmed `INIT_DATA_VALID`, `START_PARAM_RESOLVED`,
  `IDENTITY_RECEIVED`, `BIND_REQUEST_SENT`, `BIND_SUCCESS` и
  `CHANNEL_OPEN_ACCEPTED_BY_BACKEND` через отдельный bounded telemetry helper.
- События ограничены 64 telemetry records на visit и дедуплицируются ключом
  `visit + event_type` (ошибки дополнительно учитывают error class/stage).
- Сохраняются только low-cardinality device/browser/referrer/target shape,
  status/error class и timestamps. Raw yclid, startapp, initData, MAX user_id,
  cookies, полный UA/referrer и query target не сохраняются в telemetry.
- Добавлен owner-scoped read-only aggregate report:
  `GET /admin/traffic/telemetry-report` с фильтрами `channel_id`, `campaign`,
  `hours`. Он возвращает raw visits, unique yclid, repeated yclid, stages,
  final status, device/browser aggregates, trusted/untrusted telemetry,
  errors и avg/p50/p95 intervals. Это не отчёт о фактически нажатой кнопке или
  отрисованном канале.
- Добавлена классификация `final_status`; `PAGE_HIDDEN` без Mini App не
  объявляется доказанным handoff failure.

## Файлы

- `app/main.py`
- `miniapp/traffic.html`
- `tests/regression/test_traffic_diagnostic_stages.py`
- `tests/test_landing_ui_contract.py`
- `research/DEV_HANDOFF_TELEMETRY_2026-08-25.md`

## Миграции и env

MIGRATIONS = NONE. Использована существующая `TrafficEvent` table; отдельная
схема/колонки не требуются.

ENV_CHANGES = NONE.

## Проверки

- Targeted telemetry/landing/deep-link/mobile-contract suite:
  `41 passed, 1 deselected`.
- `PYTHONPYCACHEPREFIX=/tmp/maxibot-pyc python3 -m py_compile app/main.py`:
  PASS.
- AST parse `app/main.py` и telemetry tests: PASS.
- Full `python3 -m pytest -q`: BLOCKED при collection на системном Python
  3.9 из-за существующего baseline `str | None` в `app/core/config.py`.
  Это не ошибка telemetry-патча; нужен project Python 3.12/candidate Docker.
- Docker runtime/build в текущем окружении недоступен: Docker socket не
  запущен.

## Production/VPS

DEPLOY = PASS, API only, `135.106.166.46`.

- rollback copies сохранены до deploy: `app/main.py.rollback-20260825034222`
  и `miniapp/traffic.html.rollback-20260825034222`;
- API `/health/ready` = `ready`, API container = `healthy`;
- API `MAX_RUN_POLLING=false`;
- sole production worker `MAX_RUN_POLLING=true`, running worker count = 1;
- `/mini` = HTTP success и содержит `/api/traffic/miniapp-telemetry`.

Проверка legacy `37.252.10.67` заблокирована: текущий production SSH-ключ
получил `Permission denied (publickey,password)`. Legacy worker не объявляется
PASS до проверки владельцем/оператором VPS.

Обязательная acceptance-проверка после deploy:

1. `/health/ready` = 200/ready — PASS;
2. API `MAX_RUN_POLLING=false` — PASS;
3. единственный worker на `135.106.166.46` = `MAX_RUN_POLLING=true` — PASS;
4. legacy `37.252.10.67` worker остановлен, restart policy `no` — BLOCKED
   из-за SSH-доступа;
5. controlled sample показывает коррелированные landing/Mini App/server
   events, без side effects в attribution/Yandex;
6. отдельно проверить два свежих реальных iPhone smoke (из MAX и из Safari),
   поскольку файлы handoff/Mini App затронуты telemetry-only изменением;
7. отдельно проверить Android smoke.

## Что доказано / не доказано

PROVEN: telemetry ingress best-effort, safe-field filtering, trusted/untrusted
разделение, bounded dedupe, aggregate-only report и отсутствие telemetry-path
для conversion/attribution mutation — source-level tests PASS.

NOT_PROVEN: controlled real-traffic sample и real-device acceptance в этой
итерации. Production delivery/API health доказаны; наличие legacy worker
остаётся UNKNOWN.

BLOCKER: нужен доступ к legacy VPS для проверки single-poller и два свежих
ручных iPhone smoke (из MAX и Safari), а также controlled real-traffic sample.
Telemetry deploy выполнен, но полный release/E2E PASS до этих проверок не
заявляется.

## Rollback

Rollback copies сохранены до deploy на production VPS. При failed smoke вернуть
`app/main.py.rollback-20260825034222` и
`miniapp/traffic.html.rollback-20260825034222`, затем пересоздать только API;
worker не перезапускать без необходимости.
