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
