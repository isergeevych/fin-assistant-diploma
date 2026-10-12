"""
JsonChatRepository — история на диске (Б4.1). Учебная/локальная реализация.

  ${CHAT_STORAGE_DIR}/chats/<chat_id>/chat.json       — метаданные чата
  ${CHAT_STORAGE_DIR}/chats/<chat_id>/messages.jsonl  — одна ChatMessage на строку

Запись — только дописыванием в конец файла (append-only), без циклов
«прочитать → изменить → перезаписать»: так нельзя потерять историю при сбое
посреди записи. Soft delete — тоже дописыванием: строка
{"type": "soft_delete", "at": "..."}; при чтении всё до последнего маркера пропускается.

Путь строится только из UUID (тип проверен Pydantic/FastAPI) — path traversal невозможен.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiofiles
import aiofiles.os

from app.chat.domain import Chat, ChatMessage

SOFT_DELETE = "soft_delete"


class JsonChatRepository:
    def __init__(self, base_dir: Path):
        self.root = Path(base_dir) / "chats"

    def _dir(self, chat_id: UUID) -> Path:
        return self.root / str(UUID(str(chat_id)))

    async def create_chat(self, owner_external_id: str, interface: str, system_prompt: str | None = None) -> Chat:
        chat = Chat(owner_external_id=owner_external_id, interface=interface, system_prompt=system_prompt)
        directory = self._dir(chat.id)
        await aiofiles.os.makedirs(directory, exist_ok=True)
        async with aiofiles.open(directory / "chat.json", "w", encoding="utf-8") as f:
            await f.write(chat.model_dump_json())
        return chat

    async def get_chat(self, chat_id: UUID) -> Chat | None:
        path = self._dir(chat_id) / "chat.json"
        if not await aiofiles.os.path.exists(path):
            return None
        async with aiofiles.open(path, encoding="utf-8") as f:
            return Chat.model_validate_json(await f.read())

    async def find_latest_chat(self, owner_external_id: str, interface: str) -> Chat | None:
        # Полный обход каталога — приемлемо для учебной реализации; в Postgres это индексный запрос.
        if not await aiofiles.os.path.exists(self.root):
            return None
        latest: Chat | None = None
        for entry in await aiofiles.os.listdir(self.root):
            chat = await self.get_chat(UUID(entry))
            if chat and chat.owner_external_id == owner_external_id and chat.interface == interface:
                if latest is None or chat.created_at > latest.created_at:
                    latest = chat
        return latest

    async def append_message(self, chat_id: UUID, message: ChatMessage) -> ChatMessage:
        directory = self._dir(chat_id)
        await aiofiles.os.makedirs(directory, exist_ok=True)
        async with aiofiles.open(directory / "messages.jsonl", "a", encoding="utf-8") as f:
            await f.write(message.model_dump_json() + "\n")
        return message

    async def list_messages(self, chat_id: UUID, limit: int = 50) -> list[ChatMessage]:
        path = self._dir(chat_id) / "messages.jsonl"
        if not await aiofiles.os.path.exists(path):
            return []
        async with aiofiles.open(path, encoding="utf-8") as f:
            lines = await f.readlines()
        messages: list[ChatMessage] = []
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("type") == SOFT_DELETE:
                messages = []  # всё, что было до маркера, считается удалённым
                continue
            messages.append(ChatMessage.model_validate(record))
        return messages[-limit:] if limit > 0 else []

    async def soft_delete_messages(self, chat_id: UUID) -> None:
        directory = self._dir(chat_id)
        await aiofiles.os.makedirs(directory, exist_ok=True)
        marker = {"type": SOFT_DELETE, "at": datetime.now(UTC).isoformat()}
        async with aiofiles.open(directory / "messages.jsonl", "a", encoding="utf-8") as f:
            await f.write(json.dumps(marker) + "\n")
