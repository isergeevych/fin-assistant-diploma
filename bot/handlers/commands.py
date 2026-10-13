"""
Главное меню (кнопки под полем ввода) и старые команды /start, /help, /clear, /cancel — к одним и тем же хендлерам.

Роутер регистрируется ПЕРВЫМ (bot/handlers/__init__.py): нажатие кнопки меню посреди сценария «вопрос по теме»
должно сработать как кнопка, а не уйти в сценарий как текст вопроса.
"""

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import BotSettings
from bot.handlers.common import owner_id
from bot.handlers.errors import user_message_for
from bot.keyboards.reply import BTN_HELP, BTN_NEW, BTN_RECEIPT, main_menu
from bot.services.backend_client import BackendClient

router = Router(name="commands")

HELP = (
    "Я — Финпомощник, ассистент по личным финансам. Просто напишите вопрос, например:\n"
    "• «Как получить налоговый вычет за лечение?»\n"
    "• «До какой суммы страхуются вклады?»\n"
    "• «Звонят из банка и просят код из SMS — что делать?»\n\n"
    "Кнопки внизу:\n"
    "❓ Вопрос по теме — выбрать тему, потом задать вопрос\n"
    "🧾 Разобрать чек — пришлите фото чека или PDF-выписку\n"
    "🧹 Новый диалог — забыть историю разговора\n\n"
    "Можно отправить и голосовое сообщение. Под каждым ответом — 👍/👎: так я узнаю, что получилось плохо."
)


def is_admin(message: Message, settings: BotSettings) -> bool:
    return message.from_user is not None and message.from_user.id in settings.bot_admin_ids


@router.message(CommandStart())
async def cmd_start(message: Message, backend: BackendClient, settings: BotSettings, state: FSMContext) -> None:
    await state.clear()
    try:
        await backend.get_or_create_chat(owner_id(message))
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer("Здравствуйте! " + HELP, parse_mode=None, reply_markup=main_menu(is_admin(message, settings)))


@router.message(Command("help"))
@router.message(F.text == BTN_HELP)
async def cmd_help(message: Message, settings: BotSettings) -> None:
    await message.answer(HELP, parse_mode=None, reply_markup=main_menu(is_admin(message, settings)))


@router.message(F.text == BTN_RECEIPT)
async def btn_receipt(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Пришлите фото чека (можно несколько, по одному) или PDF-выписку из банка — "
                         "разберу траты по категориям. К фото можно добавить подпись с вопросом.", parse_mode=None)


@router.message(Command("clear"))
@router.message(F.text == BTN_NEW)
async def cmd_clear(message: Message, backend: BackendClient, state: FSMContext, settings: BotSettings) -> None:
    await state.clear()
    try:
        chat_id = await backend.get_or_create_chat(owner_id(message))
        await backend.clear_messages(chat_id)
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer("Начали новый диалог — прошлый разговор я больше не учитываю.", parse_mode=None,
                         reply_markup=main_menu(is_admin(message, settings)))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Сейчас нечего отменять.", parse_mode=None)
        return
    await state.clear()
    await message.answer("Отменено.", parse_mode=None)
