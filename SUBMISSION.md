# Сдача ДЗ курса «ИИ-разработчик: от API до агентов»

Все 29 домашних заданий. Модули 1–2 — отдельные репозитории, ДЗ 3.1–6.5 — этот репозиторий: один растущий
дипломный проект «Финпомощник» (ассистент по личным финансам), каждое ДЗ добавляет слой. Проверять удобнее по
актуальной ветке `master`; метка `hw-X.Y` — коммит, которым ДЗ завершено (облачные прогоны 3.x сделаны после
модуля 5, поэтому их метки стоят позже по истории). Все данные пользователей — синтетические.

## Как выполнена работа

Код, тесты, прогоны и тексты отчётов написаны ИИ-ассистентом **Claude Code** (поэтому в коммитах —
`Co-Authored-By: Claude`). Постановка задач, ключевые решения, проверка результатов и приёмка — мои:

- тема диплома (ассистент по личным финансам), выбор провайдеров (OpenRouter), структура репозиториев и формат сдачи;
- только синтетические данные; требование понятной документации к каждому ДЗ (что сделано и почему);
- отказ от локальных моделей на GPU после сбоев видеокарты, переход на облако и бюджет облачных прогонов;
- функциональность бота: кнопки вместо команд, рассылка только с подтверждением, лимиты на пользователя
  (10 сообщений в минуту, $1 и 50 000 токенов в сутки, админы без лимита);
- ручная проверка бота в Telegram и отбор неудачных ответов, стресс-тест и попытки взлома (red team) —
  [docs/bot-testing.md](docs/bot-testing.md);
- проверка, что проект запускается на чужой машине с нуля.

Технические варианты (модели эмбеддингов и судьи, параметры RAG, архитектура агентов) предлагал ассистент с
обоснованием и цифрами в отчётах; решение — принять вариант или изменить — оставалось за мной.

## Модуль 1 и 2 — отдельные репозитории

| ДЗ | Тема | Где | Главное |
|---|---|---|---|
| 1.3 | Ollama: локальные модели | [1-block-03-ollama-local-models-homework](https://github.com/isergeevych/1-block-03-ollama-local-models-homework) | сдано ранее |
| 1.4 | Выбор модели: eval, бенчмарк, стоимость | [1-block-04-comparison-cost-homework](https://github.com/isergeevych/1-block-04-comparison-cost-homework) | сдано ранее |
| 1.5 | HuggingFace + Gradio | [ai-course-hf-sentiment](https://github.com/isergeevych/ai-course-hf-sentiment) (метка `hw-1.5`) | 3 модели HF: лучшая 83,3 % / 1,4 мс; облако 91,7 % / 0,7 с |
| 2.1–2.5 | LLM API, промпты, надёжность, кеш, CLI | [ai-course-module-2](https://github.com/isergeevych/ai-course-module-2) (метка `hw-2.1-2.5`) | см. README репозитория |

## Модуль 3 — каркас сервиса

| ДЗ | Тема | Где смотреть | Главное |
|---|---|---|---|
| 3.1 | Function calling | [README §3.1](README.md#дз-31--прогоны-function-calling), `app/tools/`, `app/llm/client.py` | 4 кейса на gpt-5.4-mini; резервный источник курса ЦБ |
| 3.2 | Архитектурный паспорт, ADR, LiteLLM | [docs/architecture.md](docs/architecture.md), [docs/litellm/](docs/litellm/README.md) | fallback на claude-haiku при отказе primary |
| 3.3 | Async-клиент, бенчмарк | `app/services/llm_client.py`, [scripts/benchmark_results.md](scripts/benchmark_results.md) | ×5,0 (c=5), ×9,2 (c=10) |
| 3.4 | FastAPI-сервис | `app/main.py`, `app/routers/` | кеш 1,12 → 0,21 с, 502 `llm_auth` |
| 3.5 | Docker | `Dockerfile`, `compose.yaml` | multi-stage, non-root, healthcheck |
| 3.6 | Логи и трейсы | `app/observability/`, [docs/observability/](docs/observability/README.md) | Phoenix, маскирование PII |
| 3.7 | Тесты и G-Eval | `tests/`, `eval/`, `eval/runs/` | 4,18 → 4,95 из 5 за 3 итерации; пороги ✓ |
| 3.8 | Безопасность, garak | `app/services/security/`, [docs/security/](docs/security/garak_after_2026-10-13.md) | инъекции 20 % → 0 %, base64 80 % → 2 % |

## Модуль 4 — чат и Telegram-бот

| ДЗ | Тема | Где смотреть | Главное |
|---|---|---|---|
| 4.1 | Чат с историей | `app/chat/`, `alembic/`, [docs/chat.md](docs/chat.md) | JSON и Postgres, окно контекста |
| 4.2 | Telegram-бот | `bot/` | aiogram 3, тонкий клиент; меню кнопками |
| 4.3 | Мультимодальность | `app/chat/media.py`, `bot/handlers/media.py` | фото, PDF, DOCX, голос (с ключом OpenAI) |
| 4.4 | Модерация, админка, фидбек, рассылки | `app/moderation/`, `app/admin/`, `bot/handlers/admin.py` | рассылка с подтверждением, лимиты на пользователя |

## Модуль 5 — RAG

| ДЗ | Тема | Где смотреть | Главное |
|---|---|---|---|
| 5.1 | Эмбеддинги | [docs/embeddings.md](docs/embeddings.md) | qwen3-embedding 0.6B |
| 5.2 | Qdrant | [docs/vector_store.md](docs/vector_store.md) | фильтры, архивный тариф |
| 5.3 | RAG на LlamaIndex | [docs/rag.md](docs/rag.md) | LlamaIndex против «руками» |
| 5.4 | Chunking и re-ranker | [docs/chunking_experiment.md](docs/chunking_experiment.md) | MRR 0,951 → 1,0 |
| 5.5 | RAG-ассистент | `app/services/rag.py`, `app/services/ingestion.py` | цитаты, честный отказ, загрузка документов |
| 5.6 | Оценка RAG | [docs/rag_evaluation.md](docs/rag_evaluation.md), `tests/eval/` | faithfulness 0,922, relevancy 0,726, has_citation 1,0 |

## Модуль 6 — агенты

| ДЗ | Тема | Где смотреть | Главное |
|---|---|---|---|
| 6.1 | Наивный агент | `app/services/agent_naive.py`, [docs/agent-naive-traces/](docs/agent-naive-traces/README.md) | 6 задач, без циклов и галлюцинаций tool |
| 6.2 | ReAct + критик | `app/services/agent_react.py`, [docs/agent-react-report.md](docs/agent-react-report.md) | naive 4/5, react 5/5 |
| 6.3 | LangGraph | `app/services/agent_graph.py`, [docs/agent-graph-report.md](docs/agent-graph-report.md) | custom ≈ prebuilt, на 41 % дешевле 6.2 |
| 6.4 | Persistence и HIL | `app/services/agent_persistent.py`, [docs/agent-persistent-report.md](docs/agent-persistent-report.md) | пауза пережила рестарт контейнера; письмо ровно одно |
| 6.5 | Мультиагент или один агент | `experiments/`, [docs/multi-agent-report.md](docs/multi-agent-report.md) | ×2,8 токенов без прироста качества → один агент |

## Дополнительно

- [docs/bot-testing.md](docs/bot-testing.md) — стресс-тест бота (75 сообщений) и red team (53 атаки, обходов 0),
  список доработок.
- Тесты: `pytest -q` — 192 теста без сети и ключей.
- Запуск всего стека — [README](README.md#запуск).
