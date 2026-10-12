"""
Б3.8 — проверка ответа модели ПОСЛЕ генерации.

1. Canary: случайная метка, которая подсажена в системное сообщение. Появилась
   в ответе → модель пересказывает системный промпт → ответ режется.
2. Дословная утечка начала системного промпта (с нормализацией пробелов).
3. PII в ответе маскируется тем же redact_pii, что и в логах (Б3.6), —
   плейсхолдеры совпадают.
"""

from app.observability.pii import redact_pii

LEAK_PREFIX_CHARS = 80


class OutputBlockedError(ValueError):
    pass


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def filter_output(answer: str, system_prompt: str, canary: str) -> str:
    if canary and canary.lower() in answer.lower():
        raise OutputBlockedError("system_prompt leakage: canary detected")
    head = _norm(system_prompt)[:LEAK_PREFIX_CHARS]
    if head and head in _norm(answer):
        raise OutputBlockedError("system_prompt leakage: prefix detected")
    return redact_pii(answer)
