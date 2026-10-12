"""
HTTP-контракт чатов (Б4.1). Стабилен до конца курса: поверх него — Telegram-бот (М4Б2),
RAG (М5), tools агентов (М6).

  POST   /chats                       создать чат (reuse_existing=true — вернуть последний чат клиента)
  GET    /chats/{chat_id}             метаданные, 404 если нет
  POST   /chats/{chat_id}/messages    сообщение → ответ SSE-стримом
  GET    /chats/{chat_id}/messages    история в хронологическом порядке
  DELETE /chats/{chat_id}/messages    soft delete истории (/clear)

Доступ по chat_id: UUID не угадать, а сервис доступен только внутренней сети и боту;
проверка владельца по токену появляется вместе с admin/auth в Б4.4.
"""

from collections.abc import AsyncIterator
from uuid import UUID

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.chat.deps import ChatServiceDep
from app.chat.domain import Chat, ChatMessage
from app.core.exceptions import LLMError
from app.core.sse import DONE, sse_json, sse_text
from app.schemas.chat import ErrorResponse

router = APIRouter(prefix="/chats", tags=["chats"])
log = structlog.get_logger("chat.routes")

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Чат не найден"}}


class CreateChatIn(BaseModel):
    owner_external_id: str = Field(min_length=1, max_length=128, examples=["tg:100001"])
    interface: str = Field(min_length=1, max_length=32, examples=["telegram"])
    system_prompt: str | None = Field(default=None, max_length=4000)
    reuse_existing: bool = Field(default=False, description="Вернуть последний чат этого клиента, если он есть")


class CreateChatOut(BaseModel):
    chat_id: UUID


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000, examples=["Привет, меня зовут Аня"])


@router.post("", response_model=CreateChatOut, summary="Создать чат")
async def create_chat(body: CreateChatIn, service: ChatServiceDep) -> CreateChatOut:
    chat = await service.create_chat(body.owner_external_id, body.interface, body.system_prompt, body.reuse_existing)
    return CreateChatOut(chat_id=chat.id)


@router.get("/{chat_id}", response_model=Chat, summary="Метаданные чата", responses=NOT_FOUND)
async def get_chat(chat_id: UUID, service: ChatServiceDep) -> Chat:
    return await service.get_chat(chat_id)


@router.post(
    "/{chat_id}/messages",
    summary="Отправить сообщение — ответ SSE-стримом",
    responses={200: {"content": {"text/event-stream": {}}}, 400: {"model": ErrorResponse}, **NOT_FOUND},
)
async def send_message(chat_id: UUID, body: MessageIn, service: ChatServiceDep) -> StreamingResponse:
    await service.get_chat(chat_id)      # 404 — до начала стрима, честным HTTP-кодом
    service.check_input(body.content)    # 400 — тоже до заголовков

    async def events() -> AsyncIterator[str]:
        try:
            async for chunk in service.send_message(chat_id, body.content):
                yield sse_text(chunk)
        except LLMError as e:
            log.warning("chat.stream_error", code=e.code)
            yield sse_json({"error": {"code": e.code, "message": e.message}})
        yield DONE

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{chat_id}/messages", response_model=list[ChatMessage], summary="История (старые → новые)",
            responses=NOT_FOUND)
async def list_messages(chat_id: UUID, service: ChatServiceDep,
                        limit: int = Query(default=50, ge=1, le=500)) -> list[ChatMessage]:
    return await service.list_messages(chat_id, limit)


@router.delete("/{chat_id}/messages", summary="Очистить историю (soft delete)", responses=NOT_FOUND)
async def clear_messages(chat_id: UUID, service: ChatServiceDep) -> dict:
    await service.clear_history(chat_id)
    return {"status": "ok"}
