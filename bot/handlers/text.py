"""Любой текст, не являющийся командой, кнопкой меню и не попавший в FSM-сценарий, уходит в backend.

Тексты кнопок меню исключены: админская кнопка, отправленная не-админом вручную, не должна уйти в LLM
как вопрос (admin-роутер её молча отфильтровал — здесь она тоже молча игнорируется).
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from bot.config import BotSettings
from bot.handlers.common import ask_backend
from bot.keyboards.reply import MENU_BUTTONS
from bot.services.backend_client import BackendClient
from bot.services.smalltalk import canned_reply

router = Router(name="text")


@router.message(StateFilter(None), F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTONS))
async def handle_text(message: Message, backend: BackendClient, settings: BotSettings) -> None:
    if (reply := canned_reply(message.text)) is not None:  # «привет», «спасибо», «???» — готовый ответ без LLM
        await message.answer(reply, parse_mode=None)
        return
    await ask_backend(message, backend, message.text, settings)
