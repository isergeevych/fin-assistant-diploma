"""
Отрисовка стрима в Telegram (Б4.2): сообщение-заглушка, затем edit_text по мере
прихода кусков. Правки дросселируются (не чаще interval_sec) — Telegram отвечает 429
на частые правки одного сообщения. Текст модели отправляется БЕЗ parse_mode:
иначе ответ LLM мог бы внедрить HTML/Markdown-разметку (ссылки, форматирование).
В Б4.3 сюда добавится нативный sendMessageDraft.
"""

import time
from collections.abc import AsyncIterator

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

TELEGRAM_LIMIT = 4096
PLACEHOLDER = "…"


async def stream_to_message(message: Message, chunks: AsyncIterator[str], interval_sec: float = 1.0) -> str:
    reply = await message.answer(PLACEHOLDER, parse_mode=None)
    buffer, shown, last_edit = "", PLACEHOLDER, 0.0
    async for chunk in chunks:
        buffer += chunk
        now = time.monotonic()
        if now - last_edit >= interval_sec and buffer.strip():
            shown = await _edit(reply, buffer, shown)
            last_edit = now
    if buffer.strip():
        await _edit(reply, buffer, shown)
    else:
        await _edit(reply, "Пустой ответ, попробуйте переформулировать вопрос.", shown)
    return buffer


async def _edit(reply: Message, text: str, shown: str) -> str:
    text = text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"
    if text == shown:
        return shown
    try:
        await reply.edit_text(text, parse_mode=None)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    return text
