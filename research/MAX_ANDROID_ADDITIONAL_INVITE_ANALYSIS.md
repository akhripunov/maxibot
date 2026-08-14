# MAX Android: поиск additional invite RPC

Дата: 2026-08-11.

## Verdict

**BLOCKED — Android APK/protocol не удалось исследовать в текущем окружении.**

Причина не в ошибке MAX-протокола, а в отсутствии Android-артефактов и среды анализа:

- локального APK MAX нет;
- `adb`, `jadx` и `apktool` не установлены;
- Android Emulator/подключённое Android-устройство не обнаружены (`adb devices` не возвращает устройств).

Официальная страница MAX направляет Android-пользователя в RuStore, Google Play и AppGallery; официальное приложение имеет package ID `ru.oneme.app`. [Официальная инструкция загрузки MAX](https://max.ru/help/about/gde-i-kak-skachat-prilozhenie-max), [Google Play](https://play.google.com/store/apps/details?id=ru.oneme.app)

Сторонние зеркала APK не использовались, потому что ТЗ ограничивает исследование официальным клиентом.

## Что подтверждено до Android-ветки

| Feature | Web | Android |
|---|---|---|
| Primary invite read | FOUND | BLOCKED |
| Primary invite rotate | operation 55 | BLOCKED |
| Additional invite create | NOT FOUND | BLOCKED |
| Invite list | NOT FOUND | BLOCKED |
| Individual invite revoke | NOT FOUND; current-link revoke only | BLOCKED |
| Join-request invite source | NOT FOUND | BLOCKED |

В Web уже найдено:

```text
operation 55
payload: { chatId, revokePrivateLink: true }
behavior: rotate/revoke current primary private link
```

Эта операция в Android-ТЗ повторно не трактуется как кандидат.

## Что необходимо для продолжения

Нужен один из безопасных вариантов:

1. официальный APK MAX, полученный из Google Play/RuStore/AppGallery, помещённый в workspace;
2. подключённый Android Emulator/устройство с авторизованным собственным тестовым аккаунтом;
3. установленный локальный `jadx`/`apktool` или готовый декомпилированный output.

После этого можно будет проверить только channel/invite классы, protobuf/RPC enum, `repeated` link-поля и динамический rotate/create call, не извлекая и не публикуя credentials.

## Текущий честный вывод

По Web additional-invite RPC не найден. По Android вывод пока **не установлен**, поэтому утверждать `Android = NO` нельзя.

