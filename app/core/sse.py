"""
Единый SSE-энкодер (решение C-4 в плане: формат стрима меняется от блока к блоку,
поэтому сериализация живёт в одном месте).

По спецификации SSE перевод строки внутри data ломает кадр. Многострочный
текст поэтому кодируется несколькими строками `data:` — клиент (EventSource,
httpx-парсер бота) склеивает их обратно через "\n".
"""

import json

DONE = "data: [DONE]\n\n"


def sse_text(text: str) -> str:
    return "".join(f"data: {line}\n" for line in text.split("\n")) + "\n"


def sse_json(payload: dict, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return prefix + f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
