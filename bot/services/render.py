"""
Отрисовка стрима в Telegram.

draft (Б4.3, по умолчанию): нативный sendMessageDraft (Bot API 10.0). Один draft_id на весь
  стрим — Telegram плавно анимирует черновик; финальный send_message фиксирует ответ
  (иначе черновик исчезнет через ~30 с). Пока ждём первый токен — «печатает…».
edit (Б4.2, запасной): сообщение-заглушка + edit_text не чаще interval_sec (Telegram даёт 429
  на частые правки одного сообщения). Используется, если draft недоступен (например, в группах).

Текст модели всегда отправляется БЕЗ parse_mode: ответ LLM не должен внедрять разметку.
"""

import time
import uuid
from collections.abc import AsyncIterator

from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

TELEGRAM_LIMIT = 4096
PLACEHOLDER = "…"
EMPTY = "Пустой ответ, попробуйте переформулировать вопрос."


def _clip(text: str) -> str:
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


async def stream_to_chat(message: Message, chunks: AsyncIterator[str], mode: str = "draft",
                         interval_sec: float = 1.0) -> str:
    if mode == "draft" and message.chat.type == "private":
        try:
            return await _stream_draft(message, chunks, min(interval_sec, 0.3))
        except TelegramBadRequest:
            pass  # draft недоступен — ниже запасной путь (стрим ещё не начат: _stream_draft падает на первом вызове)
    return await stream_to_message(message, chunks, interval_sec)


async def _stream_draft(message: Message, chunks: AsyncIterator[str], interval_sec: float) -> str:
    bot, chat_id = message.bot, message.chat.id
    draft_id = uuid.uuid4().int & 0x7FFFFFFF
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await bot.send_message_draft(chat_id=chat_id, draft_id=draft_id, text=PLACEHOLDER, parse_mode=None)
    buffer, last = "", 0.0
    async for delta in chunks:
        buffer += delta
        now = time.monotonic()
        if buffer.strip() and now - last >= interval_sec:
            await bot.send_message_draft(chat_id=chat_id, draft_id=draft_id, text=_clip(buffer), parse_mode=None)
            last = now
    await bot.send_message(chat_id=chat_id, text=_clip(buffer) if buffer.strip() else EMPTY, parse_mode=None)
    return buffer


async def stream_to_message(message: Message, chunks: AsyncIterator[str], interval_sec: float = 1.0) -> str:
    reply = await message.answer(PLACEHOLDER, parse_mode=None)
    buffer, shown, last_edit = "", PLACEHOLDER, 0.0
    async for chunk in chunks:
        buffer += chunk
        now = time.monotonic()
        if now - last_edit >= interval_sec and buffer.strip():
            shown = await _edit(reply, buffer, shown)
            last_edit = now
    await _edit(reply, buffer if buffer.strip() else EMPTY, shown)
    return buffer


async def _edit(reply: Message, text: str, shown: str) -> str:
    text = _clip(text)
    if text == shown:
        return shown
    try:
        await reply.edit_text(text, parse_mode=None)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    return text
