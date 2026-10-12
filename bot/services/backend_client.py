"""
BackendClient — тонкий async-клиент бота к chat-сервису (Б4.2, расширен в Б4.3).

  get_or_create_chat — POST /chats с reuse_existing=true (идемпотентно);
  send_message       — POST /chats/{id}/messages multipart (content + опциональный media),
                       разбор SSE JSON-событий → текстовые дельты. ЕДИНСТВЕННЫЙ метод для текста,
                       фото, голоса и документов;
  clear_messages     — DELETE /chats/{id}/messages.

Устойчивость:
- один httpx.AsyncClient на всё приложение (создаётся в bot/__main__.py);
- таймауты: connect 3 с, read 60 с (для стрима — 120 с), write 10 с, pool 5 с;
- retry (tenacity) ТОЛЬКО на сетевые ошибки соединения — не на 4xx/5xx: 5xx мог уже
  стоить денег за LLM-вызов. Стрим, уже начавший отдавать данные, не ретраится никогда.
"""

import json
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

STREAM_TIMEOUT = httpx.Timeout(connect=3.0, read=120.0, write=10.0, pool=5.0)
RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout)


class BackendStreamError(Exception):
    """Backend сообщил об ошибке внутри уже начатого SSE-потока."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _retrying() -> AsyncRetrying:
    return AsyncRetrying(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )


class BackendClient:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def get_or_create_chat(self, owner_external_id: str, interface: str = "telegram") -> UUID:
        async for attempt in _retrying():
            with attempt:
                r = await self.http.post(
                    "/chats",
                    json={"owner_external_id": owner_external_id, "interface": interface, "reuse_existing": True},
                )
        r.raise_for_status()
        return UUID(r.json()["chat_id"])

    async def send_message(
        self, chat_id: UUID, content: str, media: bytes | None = None, mime: str | None = None,
        filename: str = "file.bin", meta: dict | None = None,
    ) -> AsyncIterator[str]:
        """meta — сюда кладётся message_id ответа из события done (нужен для 👍/👎)."""
        # content передаём частью multipart (filename=None): без файла httpx иначе отправил бы
        # application/x-www-form-urlencoded, а контракт эндпоинта — multipart/form-data всегда.
        files: list = [("content", (None, content))]
        if media:
            files.append(("media", (filename, media, mime or "application/octet-stream")))
        started = False
        async for attempt in _retrying():
            with attempt:
                if started:  # данные уже пошли пользователю — повтор означал бы второй платный ответ
                    raise RuntimeError("stream already started")
                async with self.http.stream(
                    "POST", f"/chats/{chat_id}/messages", files=files, timeout=STREAM_TIMEOUT,
                ) as r:
                    if r.status_code >= 400:
                        await r.aread()
                        r.raise_for_status()
                    async for event in iter_sse_data(r.aiter_lines()):
                        payload = json.loads(event)
                        kind = payload.get("type")
                        if kind == "token":
                            started = True
                            yield payload["delta"]
                        elif kind == "replace":  # Б4.4: модерация вывода — показанный текст заменить
                            raise BackendStreamError("moderation_output", payload.get("text", ""))
                        elif kind == "error":
                            raise BackendStreamError(payload.get("code", "error"), payload.get("message", ""))
                        elif kind == "done":
                            if meta is not None:
                                meta["message_id"] = payload.get("message_id")
                            return

    async def send_feedback(self, chat_id: UUID, message_id: UUID, value: str) -> None:
        r = await self.http.post(f"/chats/{chat_id}/messages/{message_id}/feedback", json={"value": value})
        r.raise_for_status()

    # ---------- Б4.4: admin и рассылки ----------

    async def admin_get(self, path: str, admin_token: str, **params) -> dict | list:
        r = await self.http.get(f"/chats/admin/{path}", params=params, headers={"X-Admin-Token": admin_token})
        r.raise_for_status()
        return r.json()

    async def admin_broadcast(self, message: str, admin_token: str) -> dict:
        r = await self.http.post("/chats/admin/broadcast", json={"message": message, "interface_filter": "telegram"},
                                 headers={"X-Admin-Token": admin_token})
        r.raise_for_status()
        return r.json()

    async def pending_broadcasts(self, internal_token: str) -> list[dict]:
        r = await self.http.get("/internal/broadcasts/pending", headers={"X-Internal-Token": internal_token})
        r.raise_for_status()
        return r.json()

    async def mark_broadcast_done(self, broadcast_id: int, sent_count: int, internal_token: str) -> None:
        r = await self.http.post(f"/internal/broadcasts/{broadcast_id}/done", json={"sent_count": sent_count},
                                 headers={"X-Internal-Token": internal_token})
        r.raise_for_status()

    async def clear_messages(self, chat_id: UUID) -> None:
        async for attempt in _retrying():
            with attempt:
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
    if buffer:
        yield "\n".join(buffer)
