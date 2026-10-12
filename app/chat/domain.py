"""
Доменные модели чата (Б4.1). Только Pydantic и стандартная библиотека —
модели не знают ни про SQLAlchemy, ни про FastAPI, ни про файлы.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant", "system"]


def _now() -> datetime:
    return datetime.now(UTC)


class ChatMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    chat_id: UUID
    role: Role
    content: str
    tokens: int | None = None
    created_at: datetime = Field(default_factory=_now)


class Chat(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    # Внешний идентификатор клиента: Telegram chat.id строкой, email web-юзера, UUID устройства
    owner_external_id: str
    interface: str  # telegram / web / cli
    system_prompt: str | None = None
    created_at: datetime = Field(default_factory=_now)
