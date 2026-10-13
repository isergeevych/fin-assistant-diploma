"""
Главное меню — постоянная клавиатура под полем ввода (ReplyKeyboard), вместо /команд.

Тексты кнопок — константы: по ним же хендлеры узнают нажатие (F.text == BTN_…), поэтому подпись меняется
в одном месте. Админские кнопки показываются только администраторам (BOT_ADMIN_IDS) — и на всякий случай
проверяются ещё раз фильтром IsAdmin в admin-роутере: подделать нажатие можно, просто отправив такой текст.
"""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_ASK = "❓ Вопрос по теме"
BTN_RECEIPT = "🧾 Разобрать чек"
BTN_NEW = "🧹 Новый диалог"
BTN_HELP = "ℹ️ Что я умею"
BTN_STATS = "📊 Статистика"
BTN_USERS = "👥 Пользователи"
BTN_BROADCAST = "📣 Рассылка"

USER_BUTTONS = (BTN_ASK, BTN_RECEIPT, BTN_NEW, BTN_HELP)
ADMIN_BUTTONS = (BTN_STATS, BTN_USERS, BTN_BROADCAST)
MENU_BUTTONS = USER_BUTTONS + ADMIN_BUTTONS


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_ASK), KeyboardButton(text=BTN_RECEIPT)],
            [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_HELP)]]
    if is_admin:
        rows.append([KeyboardButton(text=b) for b in ADMIN_BUTTONS])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder="Спросите о деньгах, налогах, кредитах…")
