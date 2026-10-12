"""
Б4.4 — фидбек 👍/👎 на ответы ассистента (только Postgres).

Владелец голоса берётся из ЧАТА на сервере, а не из запроса клиента, и сообщение обязано
принадлежать этому чату и быть ответом ассистента. Подделанный callback_data в Telegram
(fb:up:<чужой id>) поэтому ничего не даст. UNIQUE(owner_external_id, message_id) +
ON CONFLICT: повторное нажатие меняет голос, а не плодит дубли.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.repositories.pg_models import ChatMessageRow, ChatRow, MessageFeedbackRow


class FeedbackError(LookupError):
    pass


async def save_feedback(session: AsyncSession, chat_id: UUID, message_id: UUID, value: Literal["up", "down"],
                        sources: list[str] | None = None) -> None:
    row = await session.execute(
        select(ChatRow.owner_external_id, ChatMessageRow.role, ChatMessageRow.media_refs)
        .join(ChatMessageRow, ChatMessageRow.chat_id == ChatRow.id)
        .where(ChatRow.id == chat_id, ChatMessageRow.id == message_id, ChatMessageRow.deleted_at.is_(None))
    )
    found = row.first()
    if found is None or found.role != "assistant":
        raise FeedbackError("сообщение не найдено в этом чате")
    # Источники: присланные клиентом сверяются с тем, что реально было показано (сохранено в ответе),
    # чужие имена файлов отбрасываются; если клиент не прислал — берём сохранённые.
    stored = [s.get("file_name") for s in (found.media_refs or {}).get("sources", [])]
    shown = [s for s in (sources or stored) if s in stored] if stored else None
    stmt = insert(MessageFeedbackRow).values(message_id=message_id, owner_external_id=found.owner_external_id,
                                             value=value, shown_sources=shown)
    stmt = stmt.on_conflict_do_update(constraint="uq_feedback_owner_message",
                                      set_={"value": value, "shown_sources": shown})
    await session.execute(stmt)
    await session.commit()
