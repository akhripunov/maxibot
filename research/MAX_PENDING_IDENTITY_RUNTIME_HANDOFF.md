# Handoff: MAX pending identity via Runtime instrumentation

Дата: 2026-08-13.

## Verdict

```text
PENDING_USER_ID_VERIFIED = NO
```

## Что сделано

- подключена уже авторизованная Chrome/MAX Web вкладка;
- через CDP Runtime включены `Runtime.enable`, `Debugger.enable`,
  `Network.enable`;
- проверены DOM-якорь и текущий UI state;
- проверены глобальные runtime keys по `pending`, `join`, `member`, `store`,
  `state`, `request`;
- исследован загруженный MAX Svelte-код без изменения production runtime;
- approve-кнопка не нажималась, новые заявки не создавались, opcode 77 не
  отправлялся.

## Evidence

Текущий UI содержит блок заявок, но состояние уже пустое:

```text
Заявки на вступление
Нет заявок
Вы разобрали все заявки
```

DOM по-прежнему содержит имя «Любимая Печенька» в ранее отрендеренных/связанных
элементах, но живой pending-row с user identity отсутствует.

Runtime-приложение — Svelte; React fiber/Redux global store не обнаружены.
Глобальные runtime keys не содержат доступного pending store.

В загруженном MAX chunk обнаружен фактический UI/data path:

```text
send(59, {
  query,
  type: "JOIN_REQUEST",
  chatId,
  marker,
  count: 50
})
→ response.members[].contact
→ contacts.upsert(contact)
```

Предыдущий sanitized capture response содержал `members[].contact.id` и имя
«Любимая Печенька», но это capture-level evidence, а не текущий decoded
Runtime object, извлечённый из живого UI state.

## Почему identity не объявлена доказанной в этом handoff

По строгому условию задания нужно одновременно получить из runtime object:

```text
displayName = Любимая Печенька
chatId = target channel
type = JOIN_REQUEST
contact.id/userId = X
```

После предыдущего approve pending object больше не существует в текущей сессии.
Извлекать X из старых raw/private кадров в качестве нового runtime proof нельзя.

## Изменённые файлы

- добавлен только этот handoff-отчёт;
- `MaxWebSession`, `MaxAutoApproveWorker`, Attribution Engine, БД и миграции
  не изменялись.

## Команды/проверки

- CDP Runtime/Debugger/Network enable на авторизованной вкладке;
- Runtime DOM inspection по имени pending user;
- безопасный поиск runtime globals;
- чтение загруженных JS chunks с фильтром `pendingJoinRequest/JOIN_REQUEST`.

## Security

Approve не выполнялся. Новые заявки не создавались. Токен, cookies, auth frames
и session credentials в отчёт не включались и в Git не добавлялись.

## Blocker

Для завершения именно Runtime instrumentation нужен новый pending user и capture
до его обработки, либо повторное появление pending state в текущей вкладке.
Текущий handoff намеренно не создаёт такую заявку автоматически.
