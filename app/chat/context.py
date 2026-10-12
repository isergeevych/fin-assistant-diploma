"""
Сборка контекста для LLM из истории чата (Б4.1).

Стратегия — sliding window: последние N сообщений (CHAT_CONTEXT_WINDOW) +
системный промпт в начале. Обоснование — docs/chat.md: вопросы к финансовому
ассистенту короткие и почти независимые, а данные о тратах ассистент берёт
из tools, а не из памяти диалога.

Поверх окна — токен-бюджет: CONTEXT − RESPONSE − SAFETY_MARGIN. Если окно не
влезает, старые сообщения отрезаются с начала; system-сообщения и последний
вопрос пользователя не трогаются никогда.

count_tokens — tiktoken o200k_base (семейство GPT-4o/GPT-5) + ChatML-накладные
расходы: +4 токена на сообщение и +2 на ответ (ориентир OpenAI cookbook).
"""

from functools import lru_cache

import tiktoken

from app.chat.domain import ChatMessage

PER_MESSAGE_OVERHEAD = 4
REPLY_PRIMING = 2


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("o200k_base")


def count_text_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def count_tokens(messages: list[dict]) -> int:
    return sum(PER_MESSAGE_OVERHEAD + count_text_tokens(m.get("content") or "") for m in messages) + REPLY_PRIMING


def fit_to_budget(messages: list[dict], budget: int) -> list[dict]:
    """Режет историю с начала, сохраняя все system-сообщения и последнее сообщение."""
    if count_tokens(messages) <= budget:
        return messages
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    while len(rest) > 1 and count_tokens(system + rest) > budget:
        rest.pop(0)
    return system + rest


def sliding_window(history: list[ChatMessage], window: int) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in history[-window:] if m.role != "system"]
