# Персистентный агент с human-in-the-loop (ДЗ 6.4)

Код — `app/services/agent_persistent.py` (граф, checkpointer, журнал отправок), `app/routers/agent.py`
(`POST /agent/stream`), `scripts/time_travel_demo.py`, тесты — `tests/test_agent_persistent.py` (7 тестов на
`AsyncSqliteSaver(":memory:")`) и `tests/app/test_agent_api.py`. Граф 6.3 (`agent_graph.py`) не тронут.

## 1. Backend по режимам

| `AGENT_CHECKPOINTER` | Где | Почему |
|---|---|---|
| `memory` | unit-тесты (`tests/conftest.py`) | ничего не пишет на диск, быстрее всего |
| `sqlite` (по умолчанию) | локальная разработка | один файл `var/agent_checkpoints.sqlite`, без сервера; переживает перезапуск процесса |
| `postgres` | docker-compose | тот же Postgres, что у истории чата: бэкапы, конкурентные воркеры, общий источник правды |

Выбор — в `agent_lifespan()`; он же вызывает `await saver.setup()` **один раз** при старте FastAPI (из lifespan в
`app/main.py`), а не на каждый запрос. Недоступность БД не роняет сервис: `/agent/stream` отвечает 503.
Локально Postgres-режим на Windows не годится: psycopg 3 в async не работает с `ProactorEventLoop` — поэтому
локальный дефолт sqlite, а Postgres — в контейнере (Linux).

## 2. Postgres в docker-compose

- В сервис `app` добавлены `AGENT_CHECKPOINTER: postgres` и `SMTP_HOST: mailpit`; новый сервис — `mailpit`
  (SMTP-ловушка, UI на `127.0.0.1:8025`); в образ добавлен extra `agents`.
- URI — тот же `DATABASE_URL`, что у чата (`postgresql+asyncpg://fin:…@postgres:5432/fin`), с заменой драйвера на
  psycopg 3: `postgres_uri()` → `postgresql://…`. Отдельную БД не заводили.
- Alembic не трогает служебные таблицы: `include_name` в `alembic/env.py` исключает `checkpoints`,
  `checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations` (доменную схему ведёт Alembic, схему
  чекпоинтера — `setup()`).
- Проверка после `docker compose up -d`:

```
$ docker compose exec postgres psql -U fin -d fin -c '\dt'
 public | alembic_version       | table | fin
 public | broadcast_queue       | table | fin
 public | chat_messages         | table | fin
 public | chats                 | table | fin
 public | checkpoint_blobs      | table | fin
 public | checkpoint_migrations | table | fin
 public | checkpoint_writes     | table | fin
 public | checkpoints           | table | fin
 public | message_feedback      | table | fin
 public | moderation_incidents  | table | fin
```

## 3. Опасный tool и идемпотентность

`send_report_email(to, subject, body)` — письмо клиенту необратимо: отправленное не отзовёшь, а ошибка в сумме
или адресе — это утечка финансовых данных не тому человеку. Модель tool только «запрашивает» — router уводит
вызов в два отдельных узла, связанных ребром:

```
call_model ──(tool_call send_report_email)──▶ prepare_email ──▶ confirm_and_execute_email ──▶ call_model
```

- **До `interrupt()`** (`prepare_email` и начало `confirm_and_execute_email`): проверка адреса, обрезка темы и
  текста, сборка черновика, **детерминированный** id = `thread_id:tool_call_id` (без `uuid4` — при повторном
  выполнении узла id тот же), чтение роли из `config`. Ни одного внешнего вызова.
- **После `interrupt()`**: при `resume=True` — проверка журнала отправок и реальная отправка по SMTP; при
  `resume=False` — только ToolMessage «пользователь отклонил».

Почему так: при resume LangGraph запускает узел с interrupt **с начала**. Если бы отправка стояла до `interrupt()`,
письмо ушло бы при паузе и ещё раз при продолжении.

**Что оказалось недостаточно (найдено в time travel, §5).** Даже с отправкой строго после `interrupt()` replay со
старого чек-пойнта паузы повторно выполняет узел с уже сохранённым решением — и письмо уходило **второй раз**.
Состояние графа тут не защищает: на старом чек-пойнте письмо ещё «не отправлено». Решение — журнал отправок
**вне графа** (`EmailLedger`, таблица с PRIMARY KEY по id черновика): сначала `claim(id)`, потом отправка, при
ошибке — `release(id)`. Политика «не более одного раза»: лучше не отправить при сбое SMTP (агент сообщит об
ошибке), чем отправить дважды. Тест — `test_replay_from_paused_checkpoint_does_not_send_twice`.

## 4. Пауза и продолжение (живой прогон, `docs/agent-stream-curl.log`)

Модель `gpt-5.4-mini`, стек в Docker, checkpointer — Postgres. Между паузой и решением **контейнер перезапущен**
(`docker compose restart app`) — граф продолжил с того же места из Postgres.

```
$ curl -N -X POST localhost:8010/agent/stream -d '{"thread_id":"demo-curl-1","message":"Найди в базе, до какой суммы АСВ страхует вклады, и отправь это коротким письмом на client@example.com"}'
data: {"type": "update", "node": "call_model", "messages": [{"type": "ai", "content": "Сначала найду в базе точную информацию про лимит страхования вкладов АСВ…", "tool_calls": ["search_knowledge_base"]}], …}
data: {"type": "update", "node": "execute_tool", "messages": [{"type": "tool", "content": "[deposit_insurance_asv.pdf] Страхование вкладов (АСВ)…"}]}
data: {"type": "update", "node": "call_model", "messages": [{"type": "ai", "content": "Теперь подготовлю короткий текст письма…", "tool_calls": ["send_report_email"]}], …}
data: {"type": "update", "node": "prepare_email", "email_draft": {"to": "client@example.com", "subject": "Лимит страхования вкладов АСВ"}}
data: {"type": "interrupt", "value": {"type": "approve_send_report_email", "preview": {"to": "client@example.com", "subject": "Лимит страхования вкладов АСВ", "body": "АСВ страхует вклады физических лиц … не более 1,4 млн ₽ …"}, "draft_id": "demo-curl-1:call_YTp4…"}}
data: {"type": "done", "next": ["confirm_and_execute_email"], "sent": null}

# docker compose restart app

$ curl -N -X POST localhost:8010/agent/stream -d '{"thread_id":"demo-curl-1","resume":true}'
data: {"type": "update", "node": "confirm_and_execute_email", "email_draft": null, "sent": true, "messages": [{"type": "tool", "content": "Отправлено после подтверждения. Кому: client@example.com, тема: «Лимит страхования вкладов АСВ»."}]}
data: {"type": "update", "node": "call_model", "messages": [{"type": "ai", "content": "Письмо отправлено на client@example.com. Коротко по сути: АСВ страхует вклады на 100% суммы, но не более 1,4 млн ₽…"}]}
data: {"type": "update", "node": "force_finish"}
data: {"type": "done", "next": [], "sent": true}

$ (тот же resume ещё раз) → HTTP 409: нет паузы, ожидающей решения
```

Письмо в Mailpit — ровно одно, Message-ID `demo-curl-1.call_YTp4…@finpomoshnik.local` (`docs/agent-mailpit.png`).
Токены ответа (`"type": "token"`, 52 и 51 событие) в листинге опущены.

## 5. Time travel (`python -m scripts.time_travel_demo`, полный вывод — `docs/agent-time-travel.log`)

```
2) История чек-пойнтов (новые сверху)
   checkpoint_id                          step  next
   1f1c6971-63e7-…-8002-…                    2  ('confirm_and_execute_email',)
   1f1c6971-63e7-…-8001-…                    1  ('prepare_email',)
   1f1c6971-63e7-…-8000-…                    0  ('call_model',)
   1f1c6971-63e7-…-bfff-…                   -1  ('__start__',)

3) Состояние на прошлом чек-пойнте (до отправки)
   next=('confirm_and_execute_email',)  sent=False  draft.subject='Траты за сентябрь 2026'

4) Две ветки из одинакового входа (разные thread_id)
   demo-approve: resume=True  → sent=True   «Итог: Отправлено после подтверждения…»
   demo-reject:  resume=False → sent=False  «Итог: Пользователь отклонил отправку — письмо не ушло…»
   отправлено писем: 1

5) Нюанс: повторный resume ТОГО ЖЕ чек-пойнта паузы с другим решением
   demo-approve @ 1f1c6971-63e7…: resume=False → sent=True
   отправлено писем всего: 1
```

Две ветки получены на **разных** `thread_id` с одинаковым входом. Пункт 5 подтверждает поведение из задания:
повторный resume того же чек-пойнта с `False` вернул **первое** решение (`sent=True`) — resume доставляет значение
в interrupt, а не переигрывает решение. Первая версия кода на этом же шаге **отправила письмо второй раз**
(`отправлено писем всего: 2 ['demo-approve:call_1', 'demo-approve:call_1']`) — отсюда журнал отправок из §3.

## 6. Режимы стриминга

`graph.astream(stream_mode=["updates", "messages"])`:

- `updates` — после каждого узла: что агент сделал (вызвал поиск, подготовил письмо). Это прогресс для
  пользователя и место, где виден `__interrupt__`;
- `messages` — токены модели по мере генерации (фильтр по узлу `call_model`), чтобы ответ «печатался».

`astream_events(version="v2")` даёт всё дерево событий (старт/конец каждого runnable, каждого tool, retriever),
но объём в разы больше, формат — внутренний LangChain, и клиенту пришлось бы самому выбирать нужное. Для
клиента (бот, веб) нужны ровно две вещи — шаги и токены, поэтому `astream` с двумя режимами. Сообщения
LangChain не сериализуются в JSON — `_summarize()` отдаёт тип, текст (≤ 300 символов) и имена tools.

## 7. Политика прав

Роль определяет сервер, а не клиент: валидный `X-Admin-Token` → `full` (отправка без подтверждения — для
служебных сценариев), иначе `AGENT_DEFAULT_ROLE` = `write-with-approve` (каждое письмо — через подтверждение),
`read-only` отправлять не может вовсе (ToolMessage «отправка запрещена»); роль передаётся в
`config["configurable"]["user_role"]` и проверяется в `confirm_and_execute_email` (тест `test_role_policy`).

## 8. Что осталось хрупким

- **Журнал отправок — SQLite-файл в томе контейнера**, а не таблица Postgres: при двух репликах `app` у каждой
  свой журнал. Для продакшена — таблица в той же БД с UNIQUE по id черновика.
- **«Не более одного раза»**: если процесс упадёт между `claim()` и фактической отправкой, письмо не уйдёт, а
  журнал будет считать его отправленным. Нужен статус `sending/sent` и разбор зависших записей.
- **`tool_results` накапливается** через `operator.add` на весь тред — у долгоживущего треда (бот) state растёт
  без предела; нужна обрезка истории (как окно контекста в чате 4.1).
- **thread_id задаёт клиент.** Любой, кто знает чужой `thread_id`, может продолжить чужой тред (и одобрить
  чужое письмо). Для бота id должен браться из аутентифицированного `chat_id` (`tg-{chat_id}`), а в API —
  связываться с пользователем.
- `search_knowledge_base` синхронный (LlamaIndex + re-ranker) — в async-графе уходит в пул потоков.
- Интеграция стрима и кнопок «Отправить / Отменить» в Telegram-бот — отдельная задача (в ДЗ достаточно curl).
