# Настройка Robokassa для MAXIBOT

## Данные магазина

Идентификатор магазина:

```env
ROBOKASSA_MERCHANT_LOGIN=maxibot.pro
```

Пароли Robokassa не хранятся в репозитории и не отправляются в чат. Их нужно
добавить только в production `.env`:

```env
ROBOKASSA_MERCHANT_LOGIN=maxibot.pro
ROBOKASSA_PASSWORD1=<сюда_Пароль_№1>
ROBOKASSA_PASSWORD2=<сюда_Пароль_№2>
ROBOKASSA_TEST_MODE=true
```

## Адреса для технических настроек магазина

После выкладки платёжного обработчика MAXIBOT указать в Robokassa:

```text
Result URL:  https://go.maxibot.pro/payments/robokassa/result
Success URL: https://go.maxibot.pro/payments/robokassa/success
Fail URL:    https://go.maxibot.pro/payments/robokassa/fail
```

Метод для Result URL: `POST`.

Для Success URL и Fail URL: `GET`.

## Тарифы MAXIBOT

```text
1 месяц   — 1 900 ₽
3 месяца  — 5 700 ₽
6 месяцев — 11 400 ₽
```

## Порядок настройки

1. В Robokassa открыть магазин `Maxibot`.
2. Открыть вкладку `Технические настройки`.
3. Проверить идентификатор `maxibot.pro`.
4. Сгенерировать и сохранить Пароль №1 и Пароль №2.
5. Добавить пароли в production `.env` по шаблону выше.
6. Не включать боевой режим до тестового платежа.
7. После выкладки MAXIBOT внести Result/Success/Fail URL.
8. Выполнить тестовый платёж на 1 месяц.
9. Проверить callback, статус заказа и активацию тарифа.

До выкладки платёжного обработчика адреса callback не активны — вводить их в
Robokassa заранее не нужно.
