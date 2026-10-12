# Бенчмарк sync vs async (Б3.3)

Дата: 2026-10-13 01:50. Модель: `openai/gpt-5.4-mini` через OpenRouter. 20 промптов «объясни одним абзацем концепцию №N», `max_tokens=400`.

| Режим | Общее время, с | Ускорение к sync | Ошибок |
|---|---|---|---|
| sync, последовательно | 35.7 | ×1.0 | 0 |
| async batch_chat, concurrency=1 | 32.7 | ×1.1 | 0 |
| async batch_chat, concurrency=5 | 7.1 | ×5.0 | 0 |
| async batch_chat, concurrency=10 | 3.9 | ×9.2 | 0 |

## batch_chat vs batch_chat_strict при ошибке на 3-м запросе

- `batch_chat` (gather, return_exceptions=True): **4 из 5** ответов получены; на позиции 3 — `LLMBadRequestError: Некорректный запрос к LLM: openai/no-such-model-xyz is not a valid model ID`
- `batch_chat_strict` (TaskGroup): **0 из 5** — `ExceptionGroup` с 1 исключением(ями): `LLMBadRequestError`; остальные задачи отменены через 0.11 с
