"""Любой текст, не являющийся командой и не попавший в FSM-сценарий, уходит в backend."""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from bot.config import BotSettings
from bot.handlers.common import ask_backend
from bot.services.backend_client import BackendClient

router = Router(name="text")


@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, backend: BackendClient, settings: BotSettings) -> None:
    await ask_backend(message, backend, message.text, settings)
