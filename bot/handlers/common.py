"""Общий путь «вопрос (+ вложение) → backend → стрим в чат» для текста, медиа и сценария /ask."""

import httpx
import structlog
from aiogram.types import Message

from bot.config import BotSettings
from bot.handlers.errors import user_message_for
from bot.services.backend_client import BackendClient, BackendStreamError
from bot.services.render import stream_to_chat

log = structlog.get_logger("bot")


def owner_id(message: Message) -> str:
    # Внешний идентификатор клиента для backend — Telegram chat.id строкой
    return str(message.chat.id)


async def ask_backend(message: Message, backend: BackendClient, text: str, settings: BotSettings,
                      media: bytes | None = None, mime: str | None = None, filename: str = "file.bin") -> None:
    try:
        chat_id = await backend.get_or_create_chat(owner_id(message))
        chunks = backend.send_message(chat_id, text, media=media, mime=mime, filename=filename)
        await stream_to_chat(message, chunks, settings.stream_mode, settings.stream_edit_interval_sec)
    except (httpx.HTTPError, BackendStreamError) as e:
        log.warning("bot.backend_error", error=type(e).__name__, tg_chat=message.chat.id)
        await message.answer(user_message_for(e), parse_mode=None)
