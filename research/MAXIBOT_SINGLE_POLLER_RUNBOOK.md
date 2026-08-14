# MAXIBOT: один production poller

## Зафиксированный инцидент

14 августа 2026 года MAXIBOT одновременно работал в двух местах:

- production worker на VPS;
- локальный контейнер `maxibot-api-1` на компьютере разработчика.

Оба процесса использовали один production MAX-аккаунт/токен и получали одни и
те же updates. Поэтому один пользовательский переход обрабатывался двумя
экземплярами с разными локальными состояниями. В интерфейсе одновременно
появлялись версии меню «1 канал / 0 ботов» и «2 канала / 1 бот», пропадал второй
канал, возникали краткие «Канал не найден» и пустые экраны при возврате.

Причина устранена: локальный poller остановлен, на VPS оставлен единственный
процесс чтения MAX updates. `gptgirls-*` к этому инциденту не относится и не
должен останавливаться или перезапускаться при работе с MAXIBOT.

## Обязательное правило

В каждый момент времени с production MAX-аккаунтом работает ровно один poller:

```text
VPS api     MAX_RUN_POLLING=false
VPS worker  MAX_RUN_POLLING=true  (единственный poller)
local api   MAX_RUN_POLLING=false
```

Локальный Docker нельзя запускать с production `.env`, если в нём включён
polling. Production MAX token должен находиться только в серверном secret/env
конфиге VPS и не должен попадать в Git, логи или документацию.

## Что закреплено в конфигурации

- `docker-compose.yml`: локальный API принудительно получает
  `MAX_RUN_POLLING=false`.
- `.env.example`: локальная настройка по умолчанию — `MAX_RUN_POLLING=false`.
- `docker-compose.vps.yml`: API имеет `false`, отдельный `worker` имеет `true`.

Не меняйте это на `true` в локальном compose. Если нужен локальный MAX-тест,
используйте stub-режим или отдельный тестовый MAX-аккаунт и запускайте его как
осознанный изолированный сценарий, не одновременно с production.

## Проверка перед локальным запуском

```bash
docker compose ps
docker compose config | rg 'MAX_RUN_POLLING|MAX_CLIENT_MODE'
```

Ожидаемо для локального API: `MAX_RUN_POLLING: "false"`. Если уже запущен
локальный контейнер MAXIBOT, остановить только его:

```bash
docker stop maxibot-api-1
```

Не использовать широкую остановку Docker и не трогать контейнеры другого
проекта (`gptgirls-*`).

## Проверка production после деплоя

На VPS должны быть запущены API и ровно один worker:

```bash
docker compose -f docker-compose.yml -f docker-compose.vps.yml ps
docker compose -f docker-compose.yml -f docker-compose.vps.yml exec api \
  python -c 'import os; print("api polling:", os.getenv("MAX_RUN_POLLING"))'
docker compose -f docker-compose.yml -f docker-compose.vps.yml exec worker \
  python -c 'import os; print("worker polling:", os.getenv("MAX_RUN_POLLING"))'
curl -fsS https://maxibot.jobjin.ru/health/ready
```

В выводе допускаются только значения флагов и health-статус; токены, cookies и
полный `.env` не выводить.

После любого изменения MAX-бота проверить, что новые updates читает только VPS
worker. Если снова появились дубли меню или разные списки каналов, первым делом
проверить второй poller, а не удалять пользовательские сообщения и не менять
модели каналов.

## Безопасный recovery

1. Остановить только обнаруженный лишний локальный `maxibot-api-*`/poller.
2. Проверить `health/ready` VPS и наличие единственного worker.
3. Повторить один тестовый переход.
4. Удалять только явно созданные MAXIBOT service-панели, если они остались;
   личные сообщения пользователя не удалять.
5. Не запускать auto-approve, opcode 77 или дополнительные MAX-сессии для
   диагностики этой проблемы.

## Acceptance checklist

- [ ] локальный compose не poll-ит MAX;
- [ ] на VPS API имеет `MAX_RUN_POLLING=false`;
- [ ] на VPS работает ровно один worker с `MAX_RUN_POLLING=true`;
- [ ] `health/ready` отвечает `200`/`ready`;
- [ ] «Мои каналы» и «Продвижение» показывают одинаковый полный список;
- [ ] переход в канал не показывает «Канал не найден»;
- [ ] возврат назад возвращает на предыдущий экран;
- [ ] `gptgirls-*` не затронут;
- [ ] secrets не попали в Git или логи.

