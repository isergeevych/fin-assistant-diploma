"""
BackendClient — тонкий async-клиент бота к chat-сервису (Б4.2).

Бот не знает про LLM и не хранит историю: всё это — в backend. Здесь только HTTP:
  get_or_create_chat — POST /chats с reuse_existing=true (идемпотентно);
  send_message       — POST /chats/{id}/messages, разбор SSE-потока → текстовые куски;
  clear_messages     — DELETE /chats/{id}/messages.

Ошибки httpx (ConnectError, ReadTimeout, HTTPStatusError) пробрасываются выше —
в handlers их превращают в понятный текст (bot/handlers/errors.py).
"""

import json
from collections.abc import AsyncIterator
from uuid import UUID

import httpx


class BackendStreamError(Exception):
    """Backend сообщил об ошибке внутри уже начатого SSE-потока."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class BackendClient:
    def __init__(self, http: httpx.AsyncClient):
        # Один httpx.AsyncClient на всё приложение (пул соединений), создаётся в bot/__main__.py
        self.http = http

    async def get_or_create_chat(self, owner_external_id: str, interface: str = "telegram") -> UUID:
        r = await self.http.post(
            "/chats",
            json={"owner_external_id": owner_external_id, "interface": interface, "reuse_existing": True},
        )
        r.raise_for_status()
        return UUID(r.json()["chat_id"])

    async def send_message(self, chat_id: UUID, content: str) -> AsyncIterator[str]:
        async with self.http.stream("POST", f"/chats/{chat_id}/messages", json={"content": content}) as r:
            if r.status_code >= 400:
                await r.aread()  # тело нужно, чтобы показать код ошибки
                r.raise_for_status()
            async for event in iter_sse_data(r.aiter_lines()):
                if event == "[DONE]":
                    return
                if event.startswith('{"error"'):
                    err = json.loads(event)["error"]
                    raise BackendStreamError(err.get("code", "error"), err.get("message", ""))
                yield event

    async def clear_messages(self, chat_id: UUID) -> None:
        r = await self.http.delete(f"/chats/{chat_id}/messages")
        r.raise_for_status()


async def iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    """Собирает SSE-события: несколько строк `data:` одного события склеиваются через \\n,
    событие заканчивается пустой строкой (спецификация SSE)."""
    buffer: list[str] = []
    async for line in lines:
        if line == "":
            if buffer:
                yield "\n".join(buffer)
                buffer = []
            continue
        if line.startswith("data:"):
            value = line[5:]
            buffer.append(value[1:] if value.startswith(" ") else value)
        # строки event:/id:/комментарии ":" в этом протоколе не используются
    if buffer:
        yield "\n".join(buffer)
