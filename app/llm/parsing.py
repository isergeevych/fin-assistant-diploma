"""Разбор ответов модели: JSON (в markdown-ограждении или без) и tool_calls."""

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_response(text: str) -> dict:
    """JSON-объект из ответа модели. Понимает ```json ... ``` и «голый» JSON с текстом вокруг.
    Невалидный JSON → ValueError (а не тихий пустой dict)."""
    if not text or not text.strip():
        raise ValueError("пустой ответ модели")
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text)] or [text]
    for candidate in candidates:
        candidate = candidate.strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"в ответе модели нет валидного JSON-объекта: {text[:80]!r}")


def parse_tool_calls(message) -> list[tuple[str, dict]]:
    """[(имя, аргументы-dict)] из ассистентского сообщения SDK. Битые аргументы → ValueError."""
    calls = []
    for tc in getattr(message, "tool_calls", None) or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"tool {tc.function.name}: аргументы не JSON") from e
        if not isinstance(args, dict):
            raise ValueError(f"tool {tc.function.name}: аргументы должны быть объектом")
        calls.append((tc.function.name, args))
    return calls
