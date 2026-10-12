"""Inline-клавиатуры. Темы — из домена диплома (личные финансы), не абстрактные."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOPICS: dict[str, str] = {
    "budget": "Бюджет и расходы",
    "taxes": "Налоги и вычеты",
    "credit": "Кредиты и вклады",
    "security": "Финансовая безопасность",
}
CANCEL = "cancel"


def topics_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for slug, title in TOPICS.items():
        kb.button(text=title, callback_data=f"topic:{slug}")
    kb.button(text="Отмена", callback_data=f"topic:{CANCEL}")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def feedback_kb(message_id: str) -> InlineKeyboardMarkup:
    """👍/👎 под ответом. callback_data ≤ 64 байт: «fb:up:» + UUID (36) = 42."""
    kb = InlineKeyboardBuilder()
    kb.button(text="👍", callback_data=f"fb:up:{message_id}")
    kb.button(text="👎", callback_data=f"fb:down:{message_id}")
    return kb.as_markup()
