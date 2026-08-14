# MAXIBOT MVP

Новый модульный MVP для каналов MAX. API MAX изолирован в `app/integrations/max_client.py`; `StubMaxClient` позволяет тестировать бизнес-логику до появления документации API.

## Запуск

```bash
cp .env.example .env
docker compose up --build
```

Mini App: http://localhost:8000/mini
Health: http://localhost:8000/health

В MVP не включены подключение пользовательских ботов и работа с токенами.
