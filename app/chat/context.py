"""
Сборка контекста для LLM из истории чата (Б4.1, мультимодальность — Б4.3).

Стратегия — sliding window: последние N сообщений (CHAT_CONTEXT_WINDOW) +
системный промпт в начале. Обоснование — docs/chat.md.

Поверх окна — токен-бюджет: CONTEXT − RESPONSE − SAFETY_MARGIN. Если окно не
влезает, старые сообщения отрезаются с начала; system-сообщения и последний
вопрос пользователя не трогаются никогда.

count_tokens — tiktoken o200k_base + ChatML-накладные расходы (+4 на сообщение, +2 итого).
Картинка в контенте оценивается фиксированно (IMAGE_TOKENS_ESTIMATE) — точная цена
зависит от разрешения и detail у провайдера.
"""

from functools import lru_cache

import tiktoken

from app.chat.domain import ChatMessage

PER_MESSAGE_OVERHEAD = 4
REPLY_PRIMING = 2
IMAGE_TOKENS_ESTIMATE = 765  # high-detail 512×512-плитки у OpenAI ≈ 765 токенов


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("o200k_base")


def count_text_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def _content_tokens(content) -> int:
    if isinstance(content, str) or content is None:
        return count_text_tokens(content or "")
    total = 0
    for part in content:  # мультимодальный контент: список content-part
        if part.get("type") == "text":
            total += count_text_tokens(part.get("text", ""))
        elif part.get("type") == "image_url":
            total += IMAGE_TOKENS_ESTIMATE
    return total


def count_tokens(messages: list[dict]) -> int:
    return sum(PER_MESSAGE_OVERHEAD + _content_tokens(m.get("content")) for m in messages) + REPLY_PRIMING


def fit_to_budget(messages: list[dict], budget: int) -> list[dict]:
    """Режет историю с начала, сохраняя все system-сообщения и последнее сообщение."""
    if count_tokens(messages) <= budget:
        return messages
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    while len(rest) > 1 and count_tokens(system + rest) > budget:
        rest.pop(0)
    return system + rest


def to_llm_message(m: ChatMessage) -> dict:
    """Сообщение истории → формат Chat Completions. Если у сообщения было вложение,
    мультимодальный контент восстанавливается из media_refs.part — модель снова «видит» фото."""
    part = (m.media_refs or {}).get("part")
    if part and m.role == "user":
        return {"role": m.role, "content": [{"type": "text", "text": m.content}, part]}
    return {"role": m.role, "content": m.content}


def sliding_window(history: list[ChatMessage], window: int) -> list[dict]:
    return [to_llm_message(m) for m in history[-window:] if m.role != "system"]
