"""
PostgresChatRepository поверх async SQLAlchemy 2.x (Б4.1).

Сессия приходит в конструктор (её жизненным циклом управляет DI) — никаких
глобальных сессий внутри класса. Все запросы параметризованы через ORM.
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.domain import Chat, ChatMessage
from app.chat.repositories.pg_models import ChatMessageRow, ChatRow


class PostgresChatRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_chat(self, owner_external_id: str, interface: str, system_prompt: str | None = None) -> Chat:
        chat = Chat(owner_external_id=owner_external_id, interface=interface, system_prompt=system_prompt)
        self.session.add(ChatRow(**chat.model_dump()))
        await self.session.commit()
        return chat

    async def get_chat(self, chat_id: UUID) -> Chat | None:
        row = await self.session.get(ChatRow, chat_id)
        return Chat.model_validate(row, from_attributes=True) if row else None

    async def find_latest_chat(self, owner_external_id: str, interface: str) -> Chat | None:
        row = await self.session.scalar(
            select(ChatRow)
            .where(ChatRow.owner_external_id == owner_external_id, ChatRow.interface == interface)
            .order_by(ChatRow.created_at.desc())
            .limit(1)
        )
        return Chat.model_validate(row, from_attributes=True) if row else None

    async def append_message(self, chat_id: UUID, message: ChatMessage) -> ChatMessage:
        self.session.add(ChatMessageRow(**message.model_dump(exclude={"chat_id"}), chat_id=chat_id))
        await self.session.commit()
        return message

    async def list_messages(self, chat_id: UUID, limit: int = 50) -> list[ChatMessage]:
        # Берём ПОСЛЕДНИЕ N (DESC + LIMIT по частичному индексу), а LLM нужен хронологический порядок → reversed
        rows = (
            await self.session.scalars(
                select(ChatMessageRow)
                .where(ChatMessageRow.chat_id == chat_id, ChatMessageRow.deleted_at.is_(None))
                .order_by(ChatMessageRow.created_at.desc(), ChatMessageRow.id.desc())
                .limit(limit)
            )
        ).all()
        return [ChatMessage.model_validate(r, from_attributes=True) for r in reversed(rows)]

    async def soft_delete_messages(self, chat_id: UUID) -> None:
        await self.session.execute(
            update(ChatMessageRow)
            .where(ChatMessageRow.chat_id == chat_id, ChatMessageRow.deleted_at.is_(None))
            .values(deleted_at=func.now())
        )
        await self.session.commit()
