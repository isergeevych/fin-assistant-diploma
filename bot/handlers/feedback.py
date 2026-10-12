"""
Б4.4 — 👍/👎 под ответом → POST /chats/{chat_id}/messages/{message_id}/feedback.

callback_data приходит от клиента и может быть подделан, поэтому:
  - голос — только из {"up", "down"}, message_id — валидный UUID;
  - чат берётся по chat.id того, кто нажал, а backend проверяет, что сообщение
    принадлежит именно этому чату (чужое сообщение → 404).
После сохранения кнопки убираются.
"""

from uuid import UUID

import httpx
from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.handlers.errors import user_message_for
from bot.services.backend_client import BackendClient

router = Router(name="feedback")
VOTES = {"up", "down"}


def parse_feedback(data: str) -> tuple[str, UUID] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fb" or parts[1] not in VOTES:
        return None
    try:
        return parts[1], UUID(parts[2])
    except ValueError:
        return None


@router.callback_query(F.data.startswith("fb:"))
async def on_feedback(callback: CallbackQuery, backend: BackendClient) -> None:
    parsed = parse_feedback(callback.data)
    if parsed is None:
        await callback.answer("Некорректная кнопка")
        return
    vote, message_id = parsed
    try:
        chat_id = await backend.get_or_create_chat(str(callback.message.chat.id))
        await backend.send_feedback(chat_id, message_id, vote)
    except httpx.HTTPError as e:
        await callback.answer(user_message_for(e), show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Спасибо за оценку!")
