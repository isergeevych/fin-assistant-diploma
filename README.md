# Финпомощник — AI-ассистент по личным финансам

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

```powershell
copy .env.example .env      # вписать LLM__API_KEY (OpenRouter); остальное — по комментариям
uv sync --extra eval --extra tracing --extra rerank --group dev
.venv\Scripts\python -m pytest -q                 # тесты без сети и ключей
docker compose up -d --build                      # migrate → ingest → app + bot + postgres + qdrant + redis + phoenix
```

- API и Swagger: http://localhost:8010/docs (порт задаётся `APP_PORT`)
- Phoenix (трейсы): http://localhost:6006
- Эмбеддинги — локальная `qwen3-embedding:0.6b` в Ollama (`ollama pull qwen3-embedding:0.6b`).

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
| 6.x | Агенты | `app/services/agent_*.py`, `docs/agent-*` |

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
