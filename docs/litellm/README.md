# Эксперимент: LiteLLM proxy как LLM Gateway (Б3.2)

Конфиг: [`config.yaml`](config.yaml) — три модели и цепочка `primary → fallback → local`.

```bash
python -m venv .venv-litellm && .venv-litellm/Scripts/pip install "litellm[proxy]"   # 1.105.0
export LITELLM_MASTER_KEY=sk-...          # без него proxy отказывается стартовать
export LITELLM_PRIMARY_KEY=...            # ключ primary (для эмуляции отказа — заведомо неверный)
export OPENROUTER_API_KEY=...             # ключ fallback
litellm --config docs/litellm/config.yaml --port 4000

curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model": "primary", "messages": [{"role": "user", "content": "Сколько будет 2+2?"}]}' -D -
```

Какая модель реально ответила, видно по заголовкам ответа: `x-litellm-model-group`,
`x-litellm-attempted-fallbacks`, `x-litellm-model-api-base`.

## Прогон 1 — оба облачных провайдера отказали (2026-10-12)

`LITELLM_PRIMARY_KEY` и `OPENROUTER_API_KEY` — заведомо неверные, то есть OpenRouter отвечает 401 и primary, и fallback.

| Запрос | `x-litellm-model-group` | `attempted-fallbacks` | `api-base` | Время |
|---|---|---|---|---|
| «Какой цвет у неба?» | `local` | 1 | `http://localhost:11434` | 9,8 с (с загрузкой модели) |
| «Сколько будет 2+2?» | `local` | 1 | `http://localhost:11434` | 3,6 с |

Клиент запросил `primary` и получил ответ, не зная о сбое: proxy сам прошёл цепочку до локальной модели.
Наблюдения:
- `qwen3:8b` в роли local вернула пустой `content`: её reasoning-режим съел весь `max_tokens`.
  Заменили local на `gemma3:4b`. Вывод для диплома: tertiary-модель должна быть без режима рассуждений
  или с ограничением reasoning — иначе «аварийный» ответ окажется пустым.
- Ошибка 401 не ретраится (`attempted-retries: 0`) — правильно, повтор с тем же ключом бессмысленен.
- Безопасность по умолчанию: без `LITELLM_MASTER_KEY` proxy **отказывается стартовать** — иначе любой
  в сети ходил бы к LLM за наш счёт.
- Найден баг LiteLLM 1.105: запрос без ключа получает **500 вместо 401** — обработчик ошибки
  авторизации импортирует необязательный пакет `prisma` и падает сам. Ещё один аргумент не тащить
  лишний слой в критический путь.

## Прогон 2 — primary работает / primary отказал → fallback (с реальным ключом)

LiteLLM 1.105.0, ключ OpenRouter настоящий, local (`gemma3:4b`) не понадобился. Запрос — «Сколько будет 2+2?».

| Сценарий | Запрос | `x-litellm-model-group` | ответила модель | `attempted-retries` | `attempted-fallbacks` | Время |
|---|---|---|---|---|---|---|
| (а) primary работает | 1-й | `primary` | `gpt-5.4-mini` | 0 | 0 | 4,79 с (холодный старт proxy) |
| (а) primary работает | 2-й | `primary` | `gpt-5.4-mini` | 0 | 0 | 0,75 с |
| (б) у primary неверный ключ | 1-й | `fallback` | `anthropic/claude-haiku-4.5` | 0 | 1 | 1,66 с |
| (б) у primary неверный ключ | 2-й | `fallback` | `anthropic/claude-haiku-4.5` | 0 | 1 | 3,09 с |

Наблюдения:
- Fallback прозрачен для клиента: он запрашивал `primary`, получил 200 и ответ; о подмене модели знает только
  по заголовкам. Для диплома это аргумент логировать фактическую модель ответа (у нас — поле `model` в
  `llm.call`), иначе при разборе инцидента не понять, кто отвечал.
- 401 у primary не ретраится (`attempted-retries: 0`) — сразу fallback, как и в нашем `AsyncLLMClient`.
- Цена fallback — лишний сетевой круг до OpenRouter: +0,9–2,3 с к ответу по сравнению с прогретым primary.
- `allowed_fails: 3` за 2 запроса не сработал — primary пробуется каждый раз; при настоящей аварии первые
  3 запроса оплачивают задержку, дальше модель «остывает» на `cooldown_time` (аналог circuit breaker).

## Вывод

Решение «пишем свой LLM-слой» и его обоснование — в [`../architecture.md`](../architecture.md#6-litellm-как-готовый-llm-gateway).
