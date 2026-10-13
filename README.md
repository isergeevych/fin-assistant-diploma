# Финпомощник — AI-ассистент по личным финансам

> **Проверяющему:** указатель по всем 29 ДЗ курса — [SUBMISSION.md](SUBMISSION.md).

Дипломный проект курса «ИИ-разработчик: от API до агентов». Один растущий репозиторий: каждое ДЗ модулей 3–6
добавляет слой к одному сервису. Все данные пользователей — **синтетические**.

Что умеет:

- отвечает на вопросы о тратах пользователя (function calling над его операциями) и о курсах ЦБ;
- ведёт диалог с историей (Postgres), понимает фото чеков, PDF и DOCX, голос (при ключе OpenAI);
- отвечает по базе знаний из 56 документов (налоги, вклады, кредиты, мошенничество, FAQ приложения) с цитатами
  и честным отказом, если ответа в базе нет;
- работает через HTTP API (FastAPI, SSE-стриминг) и Telegram-бота;
- защищён от prompt injection (валидатор ввода, canary, фильтр вывода), маскирует персональные данные в логах.

## Запуск

**Нужно:** Docker Desktop (или Docker Engine + compose v2), Python 3.12, [uv](https://docs.astral.sh/uv/), ключ
[OpenRouter](https://openrouter.ai/keys) (ответы модели — центы за десятки вопросов). Ollama — по желанию (см. ниже).

### 1. Настройка (один раз)

```bash
git clone https://github.com/isergeevych/fin-assistant-diploma.git && cd fin-assistant-diploma
python scripts/init_env.py --openrouter-key sk-or-v1-... --no-ollama
```

`init_env.py` создаёт `.env` из `.env.example` и сам генерирует пароли Postgres, Qdrant и служебные токены.
Эмбеддинги базы знаний — два варианта:

- `--no-ollama` — через OpenRouter (`baai/bge-m3`), ничего ставить не нужно;
- без флага — локально в [Ollama](https://ollama.com): `ollama pull qwen3-embedding:0.6b` (так проект и разрабатывался;
  пороги RAG откалиброваны под эту модель).

### 2. Весь сервис в Docker

```bash
docker compose up -d --build     # migrate → ingest (индексация 56 документов) → app, + postgres, qdrant, redis, phoenix, mailpit
docker compose ps                # app: healthy; migrate и ingest: exited (0) — они одноразовые
```

- API и Swagger: http://localhost:8000/docs (порт — `APP_PORT` в `.env`)
- Phoenix (трейсы): http://localhost:6006 · Mailpit (письма агента, ДЗ 6.4): http://localhost:8025
- Проверка: `curl -X POST localhost:8000/rag/query -H "Content-Type: application/json" -d '{"question":"Сколько вернут за лечение зубов?"}'`
- Telegram-бот поднимается, только если в `.env` есть `BOT_TOKEN` (от @BotFather) и `BOT_ADMIN_IDS`; без токена контейнер
  `bot` просто завершается.

### 3. Тесты (без сети и ключей)

```bash
uv sync --extra agents --group dev          # минимум; полный набор: + --extra eval --extra tracing --extra rerank
uv run pytest -q                            # ~190 тестов; с Postgres — если запущен Docker, иначе они пропускаются
```

Без `--extra agents` тесты агентов (6.3–6.4) пропускаются, сервис работает, `/agent/stream` отвечает 503.
Скрипты ДЗ (`scripts/`, `eval/`, `experiments/`) запускаются через `uv run python -m scripts.<имя>`; какой extra
нужен — в шапке каждого скрипта. На Windows в PowerShell: `.venv\Scripts\python` вместо `uv run python`.

**Если что-то не так:** порт 8000 занят — поменяйте `APP_PORT`; `ingest` завершился с ошибкой — `docker compose logs
ingest` (чаще всего недоступна Ollama: используйте `--no-ollama`); кириллица в консоли Windows — `set PYTHONIOENCODING=utf-8`.

## Где какое ДЗ

| ДЗ | Что | Где смотреть |
|---|---|---|
| 3.1 | Function calling | `app/tools/`, `app/llm/client.py`, `examples/run_tool_call.py`, раздел ниже |
| 3.2 | Архитектурный паспорт, ADR, LiteLLM | `docs/architecture.md`, `docs/litellm/` |
| 3.3 | Асинхронный клиент, бенчмарк | `app/services/llm_client.py`, `scripts/benchmark.py` |
| 3.4 | FastAPI-сервис, кеш | `app/main.py`, `app/routers/`, `app/services/llm.py` |
| 3.5 | Docker | `Dockerfile`, `compose.yaml` |
| 3.6 | Логи и трейсы | `app/observability/`, `docs/observability/` |
| 3.7 | Тесты и G-Eval | `tests/`, `eval/` |
| 3.8 | Безопасность, garak | `app/services/security/`, `docs/security/` |
| 4.1 | Чат с историей | `app/chat/`, `alembic/`, `docs/chat.md` |
| 4.2–4.4 | Telegram-бот, мультимодальность, модерация, админка | `bot/`, `app/moderation/`, `app/admin/` |
| 5.1 | Эмбеддинги | `app/services/embeddings.py`, `docs/embeddings.md` |
| 5.2 | Qdrant | `app/services/vector_store.py`, `docs/vector_store.md` |
| 5.3–5.5 | RAG: LlamaIndex, чанкинг, re-ranker, ассистент | `app/services/rag.py`, `docs/rag.md`, `docs/chunking_experiment.md` |
| 5.6 | Оценка RAG (RAGAS) | `app/eval/`, `scripts/run_eval.py`, `tests/eval/`, `docs/rag_evaluation.md` |
| 6.1 | Наивный агент | `app/services/agent_naive.py`, `docs/agent-naive-traces/` |
| 6.2 | ReAct + критик | `app/services/agent_react.py`, `docs/agent-react-report.md` |
| 6.3 | LangGraph: custom и prebuilt | `app/services/agent_graph.py`, `docs/agent-graph-report.md` |
| 6.4 | Персистентность и подтверждение человеком | `app/services/agent_persistent.py`, `app/routers/agent.py`, `docs/agent-persistent-report.md` |
| 6.5 | Мультиагент против одного агента | `experiments/`, `docs/multi-agent-report.md` |

## ДЗ 3.1 — прогоны function calling

Модель `openai/gpt-5.4-mini` через OpenRouter, 12–13.10.2026. Два tool: `get_spending_summary` (операции
пользователя; владелец берётся из контекста сервера, в схеме tool его нет) и `get_exchange_rate` (курс ЦБ РФ).
Полный вывод — `docs/tool_calls_run.txt`, JSON-лог шагов — `logs/tool_calls.jsonl`.

| Кейс | Вопрос | Что сделала модель | Раундов | Токенов |
|---|---|---|---|---|
| (а) tool нужен | «Сколько я потратил на кафе в сентябре 2026?» | `get_spending_summary(2026-09-01…30, кафе)` → «11 571,49 ₽, 7 операций, топ: Шоколадница…» | 2 | 1 710 |
| (б) tool не нужен | «Что такое финансовая подушка и какого размера?» | ответила сразу, без tool | 1 | 837 |
| (в) пограничный | «Сколько примерно стоят 100 евро в рублях?» | **пошла за курсом** `get_exchange_rate(EUR)` → «≈ 9 404 ₽ по курсу ЦБ на 13.10» | 2 | 1 559 |
| (г) цепочка | «Траты на путешествия в июле и сколько это в евро?» | два tool подряд → «61 852,28 ₽ ≈ 657,66 €» | 3 | 2 716 |

**Наблюдения**

- В пограничном случае модель предпочла свежие данные ответу «по памяти» — для финансового ассистента это
  правильно: курс из памяти модели устарел на месяцы. Заплатили за это вторым раундом (+1 вызов, ~700 токенов).
- **Арифметика у LLM ненадёжна даже на production-модели:** 61 852,28 / 94,0355 = **657,75 €**, модель написала
  657,66 €. Ошибка 9 центов — мелочь, но в финансовом продукте недопустима. Вывод: расчёты делает код
  (tool возвращает готовую сумму), модель только формулирует. Та же ошибка была на локальной модели в первом
  прогоне (651,47 вместо 651,25 €).
- **cbr.ru не отвечает с зарубежных IP** (прогон шёл через VPN) — первый прогон закончился честным «сервис ЦБ
  недоступен», модель курс не выдумала. После этого добавлен резервный источник: зеркало cbr-xml-daily.ru с тем
  же XML ЦБ; в ответе tool помечено `source: "ЦБ РФ (зеркало …)"`. Если недоступны оба — ошибка уходит модели,
  цикл не падает (тесты `tests/test_tool_call.py`).
- Модель ни разу не попыталась передать `owner_external_id`: его нет в схеме, а лишние поля отсекает
  `additionalProperties: false` — чужие операции через tool не запросить.

## ДЗ 3.3 — sync против async

20 запросов к `gpt-5.4-mini` (полная таблица — `scripts/benchmark_results.md`): последовательно 35,7 с;
async с параллельностью 5 — 7,1 с (×5,0), с 10 — 3,9 с (×9,2). Ускорение почти линейное, потому что запрос
к LLM — это ожидание сети, а не работа процессора: пока ждём один ответ, отправляем остальные. Потолок
задают лимиты провайдера (429), поэтому параллельность ограничена семафором (`max_concurrency`).

«Всё или ничего»: при ошибке в 3-м запросе из 5 `batch_chat` (gather) вернул 4 ответа и ошибку на месте
третьего, `batch_chat_strict` (TaskGroup) отменил остальные через 0,11 с. Первое — для независимых задач
(рассылка), второе — когда без одного ответа результат бессмыслен.

Попутно найдено: в текст ошибки 400 попадал сырой ответ OpenRouter с `user_id` аккаунта — и уходил клиенту
API. Теперь наружу идёт только поле `message` провайдера (тест `test_bad_request_message_hides_provider_account_data`).
