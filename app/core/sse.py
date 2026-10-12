"""
Единый SSE-энкодер (решение C-4 в плане: формат стрима меняется от блока к блоку,
поэтому сериализация живёт в одном месте).

- /chat/stream (Б3.4): сырой текст `data: <текст>`; многострочный текст — несколькими
  строками `data:` (клиент склеивает их через "\n"), затем usage и `data: [DONE]`.
- /chats/{id}/messages (с Б4.3): JSON-события `data: {"type": "token", "delta": "..."}`,
  `{"type": "done"}`, `{"type": "error", ...}` — тип события явный, и текст модели,
  начинающийся с «{», уже не спутать со служебным кадром.
"""

import json

DONE = "data: [DONE]\n\n"


def sse_text(text: str) -> str:
    return "".join(f"data: {line}\n" for line in text.split("\n")) + "\n"


def sse_json(payload: dict, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return prefix + f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_event(type_: str, **fields) -> str:
    """JSON-событие с явным типом. json.dumps экранирует переводы строк — кадр не ломается."""
    return sse_json({"type": type_, **fields})
