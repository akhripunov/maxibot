# Daily channel reports — 2026-08-20

## Behaviour

The single production MAX worker checks once a minute and, at 08:30 Moscow
time or after a restart later that day, sends one report for the preceding
Moscow calendar day to the connected channel owner in MAXIBOT.

The report contains official `user_added` and removal events persisted for the
channel, plus the aggregate subscriber count from the official `GET /chats`
response when MAX provides it.  An unsubscribe source is not inferred: until
MAX provides an official invite-source field, `Пригласительные ссылки` remains
zero and removals are counted as `Не определено`.

Delivery is persisted by `(channel_id, report_date)`, so restarts and the
minute-level scheduler do not resend a successful report.

## Production handoff

- Image: `maxibot-vps-api:20260820-dailyreports`
- Migration: `0029_daily_channel_reports`
- Health: API ready; API polling disabled; the dedicated worker polling is
  enabled.
- First catch-up run after deployment delivered one report to each of the
  three currently connected channels.  Database status: `SENT=3`; a later
  scheduler pass did not add another delivery.
- Yandex attribution and conversion sending were not changed.

## Tests

- Isolated container tests: `tests/test_daily_reports.py` — 3 passed.
- Python compilation of changed modules — passed in the production image.
