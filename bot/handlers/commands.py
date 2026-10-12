"""
/start, /help, /clear, /cancel. Роутер регистрируется ПЕРВЫМ (bot/handlers/__init__.py):
иначе текст «/cancel», отправленный во время FSM-сценария, перехватил бы FSM-хендлер.
"""

import httpx
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.handlers.common import owner_id
from bot.handlers.errors import user_message_for
from bot.services.backend_client import BackendClient

router = Router(name="commands")

HELP = (
    "Я — Финпомощник, ассистент по личным финансам.\n\n"
    "Просто напишите вопрос: «Сколько я потратил на кафе в сентябре?», «Какой курс евро?», "
    "«Как получить налоговый вычет за лечение?»\n\n"
    "Команды:\n"
    "/ask — вопрос по теме (бюджет, налоги, кредиты, безопасность)\n"
    "/clear — начать диалог с чистого листа\n"
    "/cancel — отменить текущий сценарий\n"
    "/help — эта подсказка"
)


@router.message(CommandStart())
async def cmd_start(message: Message, backend: BackendClient) -> None:
    try:
        await backend.get_or_create_chat(owner_id(message))
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer("Здравствуйте! " + HELP, parse_mode=None)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, parse_mode=None)


@router.message(Command("clear"))
async def cmd_clear(message: Message, backend: BackendClient, state: FSMContext) -> None:
    await state.clear()
    try:
        chat_id = await backend.get_or_create_chat(owner_id(message))
        await backend.clear_messages(chat_id)
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer("История очищена. Начнём с чистого листа.", parse_mode=None)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Сейчас нечего отменять.", parse_mode=None)
        return
    await state.clear()
    await message.answer("Отменено.", parse_mode=None)
