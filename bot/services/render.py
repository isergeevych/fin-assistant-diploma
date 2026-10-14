"""
Отрисовка стрима в Telegram.

draft (Б4.3, по умолчанию): нативный sendMessageDraft (Bot API 10.0). Один draft_id на весь
  стрим — Telegram плавно анимирует черновик; финальный send_message фиксирует ответ
  (иначе черновик исчезнет через ~30 с). Пока ждём первый токен — «печатает…».
edit (Б4.2, запасной): сообщение-заглушка + edit_text не чаще interval_sec (Telegram даёт 429
  на частые правки одного сообщения). Используется, если draft недоступен (например, в группах).

Ошибка ПОСЛЕ начала стрима (модерация вывода «замени текст», обрыв на стороне backend):
показанный черновик/сообщение заменяется финальным текстом об ошибке — недосказанный
или отмодерированный ответ не остаётся на экране.

Финальное сообщение может нести клавиатуру (👍/👎, Б4.4): final_markup вызывается ПОСЛЕ
стрима, когда уже известен id ответа.
Разметка: Markdown модели переводится в безопасный HTML (bot/services/markdown.py) — весь HTML из ответа
экранируется, ссылки не становятся кликабельными; если Telegram отверг разметку — отправляется чистый текст.
"""

import time
import uuid
from collections.abc import AsyncIterator, Callable

from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from bot.handlers.errors import user_message_for
from bot.services.backend_client import BackendStreamError
from bot.services.markdown import to_plain, to_telegram_html

TELEGRAM_LIMIT = 4096
PLACEHOLDER = "…"
EMPTY = "Пустой ответ, попробуйте переформулировать вопрос."
MarkupFactory = Callable[[], InlineKeyboardMarkup | None]


def _clip(text: str) -> str:
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


async def _send(method, text: str, **kwargs):
    """Отправка с HTML-разметкой; «can't parse entities» — повтор чистым текстом."""
    try:
        return await method(text=to_telegram_html(text), parse_mode="HTML", **kwargs)
    except TelegramBadRequest as e:
        if "parse" not in str(e).lower() and "entit" not in str(e).lower():
            raise
        return await method(text=to_plain(text), parse_mode=None, **kwargs)


def _final_text(buffer: str, error: BackendStreamError | None) -> str:
    if error is not None:
        return error.args[0] if error.code == "moderation_output" and error.args[0] else user_message_for(error)
    return _clip(buffer) if buffer.strip() else EMPTY


async def stream_to_chat(message: Message, chunks: AsyncIterator[str], mode: str = "draft",
                         interval_sec: float = 1.0, final_markup: MarkupFactory | None = None) -> bool:
    """True — ответ показан полностью; False — заменён сообщением об ошибке."""
    if mode == "draft" and message.chat.type == "private":
        try:
            return await _stream_draft(message, chunks, min(interval_sec, 0.3), final_markup)
        except TelegramBadRequest:
            pass  # draft недоступен — запасной путь (падает на первом же вызове, до чтения стрима)
    return await stream_to_message(message, chunks, interval_sec, final_markup)


async def _stream_draft(message: Message, chunks: AsyncIterator[str], interval_sec: float,
                        final_markup: MarkupFactory | None) -> bool:
    bot, chat_id = message.bot, message.chat.id
    draft_id = uuid.uuid4().int & 0x7FFFFFFF
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await bot.send_message_draft(chat_id=chat_id, draft_id=draft_id, text=PLACEHOLDER, parse_mode=None)
    buffer, last, error = "", 0.0, None
    try:
        async for delta in chunks:
            buffer += delta
            now = time.monotonic()
            if buffer.strip() and now - last >= interval_sec:
                await _send(bot.send_message_draft, _clip(buffer), chat_id=chat_id, draft_id=draft_id)
                last = now
    except BackendStreamError as e:
        error = e
    markup = final_markup() if (final_markup and error is None) else None
    await _send(bot.send_message, _final_text(buffer, error), chat_id=chat_id, reply_markup=markup)
    return error is None


async def stream_to_message(message: Message, chunks: AsyncIterator[str], interval_sec: float = 1.0,
                            final_markup: MarkupFactory | None = None) -> bool:
    reply = await message.answer(PLACEHOLDER, parse_mode=None)
    buffer, shown, last_edit, error = "", PLACEHOLDER, 0.0, None
    try:
        async for chunk in chunks:
            buffer += chunk
            now = time.monotonic()
            if now - last_edit >= interval_sec and buffer.strip():
                shown = await _edit(reply, buffer, shown)
                last_edit = now
    except BackendStreamError as e:
        error = e
    markup = final_markup() if (final_markup and error is None) else None
    await _edit(reply, _final_text(buffer, error), shown, markup)
    return error is None


async def _edit(reply: Message, text: str, shown: str, markup: InlineKeyboardMarkup | None = None) -> str:
    text = _clip(text)
    if text == shown and markup is None:
        return shown
    try:
        await _send(reply.edit_text, text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    return text
