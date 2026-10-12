"""
Контракт хранилища чатов (Б4.1). typing.Protocol, а не ABC: реализации не
наследуются от него явно — совместимость проверяется структурно (mypy/pyright),
поэтому фейк для тестов — просто класс с теми же методами.
"""

from typing import Protocol
from uuid import UUID

from app.chat.domain import Chat, ChatMessage


class ChatRepository(Protocol):
    async def create_chat(self, owner_external_id: str, interface: str, system_prompt: str | None = None) -> Chat: ...

    async def get_chat(self, chat_id: UUID) -> Chat | None: ...

    async def find_latest_chat(self, owner_external_id: str, interface: str) -> Chat | None:
        """Последний чат клиента в интерфейсе — для идемпотентного get_or_create в боте (Б4.2)."""
        ...

    async def append_message(self, chat_id: UUID, message: ChatMessage) -> ChatMessage: ...

    async def list_messages(self, chat_id: UUID, limit: int = 50) -> list[ChatMessage]:
        """Последние limit сообщений в ХРОНОЛОГИЧЕСКОМ порядке (старые → новые), без soft-deleted."""
        ...

    async def soft_delete_messages(self, chat_id: UUID) -> None: ...
