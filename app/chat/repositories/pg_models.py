"""
ORM-модели Postgres для чатов (Б4.1). Отдельные классы от доменных Pydantic-моделей
и наружу модуля не пробрасываются: граница — ChatMessage.model_validate(row, from_attributes=True).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ChatRow(Base):
    __tablename__ = "chats"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    interface: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        # find_latest_chat (идемпотентный get_or_create в боте) — без полного скана
        Index("ix_chats_owner_interface_created", "owner_external_id", "interface", text("created_at DESC")),
    )


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    chat_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media_refs: Mapped[dict | None] = mapped_column(JSONB)  # Б4.3
    latency_ms: Mapped[int | None] = mapped_column(Integer)  # Б4.4: время ответа модели (для admin/stats)

    __table_args__ = (
        # Частичный индекс: только «живые» сообщения — ровно под запрос list_messages
        Index(
            "ix_chat_messages_chat_created",
            "chat_id",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class MessageFeedbackRow(Base):
    """Б4.4: 👍/👎 на ответ. UNIQUE(owner, message) — один голос пользователя на сообщение."""

    __tablename__ = "message_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    owner_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    shown_sources: Mapped[list | None] = mapped_column(JSONB)  # Б5.5: какие источники видел пользователь

    __table_args__ = (
        UniqueConstraint("owner_external_id", "message_id", name="uq_feedback_owner_message"),
        CheckConstraint("value IN ('up', 'down')", name="ck_feedback_value"),
    )


class BroadcastRow(Base):
    """Б4.4: очередь рассылок от админа; бот забирает pending в фоне."""

    __tablename__ = "broadcast_queue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    interface: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_count: Mapped[int | None] = mapped_column(Integer)


class ModerationIncidentRow(Base):
    """Б4.4: инциденты модерации — без сырого текста (hash + маскированный превью)."""

    __tablename__ = "moderation_incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_external_id: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # input | output
    layer: Mapped[str] = mapped_column(Text, nullable=False)  # keywords | openai
    categories: Mapped[list] = mapped_column(JSONB, nullable=False)
    text_hash: Mapped[str] = mapped_column(Text, nullable=False)
    text_preview: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
