"""
HTTP-контракт чатов (Б4.1, Б4.3). Стабилен до конца курса: поверх него — Telegram-бот,
RAG (М5), tools агентов (М6).

  POST   /chats                          создать чат (reuse_existing=true — вернуть последний чат клиента)
  GET    /chats/{chat_id}                метаданные, 404 если нет
  POST   /chats/{chat_id}/messages       multipart: content (+ media) → ответ SSE-стримом JSON-событий
  GET    /chats/{chat_id}/messages       история в хронологическом порядке
  DELETE /chats/{chat_id}/messages       soft delete истории (/clear)
  POST   /chats/{chat_id}/system-message служебное сообщение от backend (+ уведомление в бота), X-Internal-Token

Б4.3: POST /messages мигрировал с JSON-тела на multipart/form-data — URL тот же, добавилось
опциональное поле media. Отдельного /messages/with-media нет.
SSE: data: {"type":"token","delta":"..."} … data: {"type":"done"}; ошибка — {"type":"error",...}.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Literal
from uuid import UUID

import anyio
import httpx
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.chat.deps import ChatServiceDep, DbSessionDep
from app.chat.domain import Chat, ChatMessage
from app.chat.feedback import FeedbackError, save_feedback
from app.chat.media import MediaError, SpeechToText, media_to_part
from app.chat.service import ResponseReplaced
from app.core.exceptions import LLMError, ModerationBlockedError
from app.core.sse import sse_event, sse_json
from app.deps.auth import require_internal
from app.deps.providers import SettingsDep
from app.schemas.chat import ErrorResponse
from app.services.notifier import notify_user
from app.services.usage_limits import SpendMeter, UsageLimiter, UsageLimitError, current_meter

router = APIRouter(prefix="/chats", tags=["chats"])
log = structlog.get_logger("chat.routes")

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Чат не найден"}}


class CreateChatIn(BaseModel):
    owner_external_id: str = Field(min_length=1, max_length=128, examples=["100001"])
    interface: str = Field(min_length=1, max_length=32, examples=["telegram"])
    system_prompt: str | None = Field(default=None, max_length=4000)
    reuse_existing: bool = Field(default=False, description="Вернуть последний чат этого клиента, если он есть")


class CreateChatOut(BaseModel):
    chat_id: UUID


class FeedbackIn(BaseModel):
    value: Literal["up", "down"]
    sources: list[str] | None = Field(default=None, max_length=20, description="Показанные источники (имена файлов)")


class SystemMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    notify: bool = False


def get_stt_client(settings: SettingsDep) -> SpeechToText | None:
    """Whisper — только у OpenAI напрямую; без его ключа — аудио-модель через OpenRouter тем же ключом, что ответы."""
    if settings.openai_api_key is not None:
        return SpeechToText(AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()), "whisper", "whisper-1")
    if settings.llm.api_key.get_secret_value():
        client = AsyncOpenAI(base_url=settings.llm.base_url, api_key=settings.llm.api_key.get_secret_value(),
                             timeout=settings.llm.request_timeout, max_retries=settings.llm.max_retries)
        return SpeechToText(client, "chat", settings.stt_model)
    return None


@router.post("", response_model=CreateChatOut, summary="Создать чат")
async def create_chat(body: CreateChatIn, service: ChatServiceDep) -> CreateChatOut:
    chat = await service.create_chat(body.owner_external_id, body.interface, body.system_prompt, body.reuse_existing)
    return CreateChatOut(chat_id=chat.id)


@router.get("/{chat_id}", response_model=Chat, summary="Метаданные чата", responses=NOT_FOUND)
async def get_chat(chat_id: UUID, service: ChatServiceDep) -> Chat:
    return await service.get_chat(chat_id)


@router.post(
    "/{chat_id}/messages",
    summary="Сообщение (текст + опциональное вложение) — ответ SSE-стримом",
    responses={200: {"content": {"text/event-stream": {}}}, 400: {"model": ErrorResponse},
               413: {"description": "Файл слишком большой"}, 415: {"description": "Неподдерживаемый тип файла"},
               **NOT_FOUND},
)
async def send_message(
    chat_id: UUID,
    request: Request,
    service: ChatServiceDep,
    stt: Annotated[SpeechToText | None, Depends(get_stt_client)],
    content: str = Form(..., min_length=1, max_length=4000),
    media: UploadFile | None = File(None),
) -> StreamingResponse:
    # Всё, что может отказать, — ДО начала стрима: так клиент получит честный HTTP-код.
    chat = await service.get_chat(chat_id)
    limiter: UsageLimiter | None = getattr(request.app.state, "usage_limiter", None)
    if limiter is not None:  # лимиты на пользователя — до любых платных вызовов (распознавание, модель)
        try:
            await limiter.check(chat.owner_external_id)
        except UsageLimitError as e:
            raise HTTPException(status_code=429, detail={"code": e.code, "message": e.message},
                                headers={"Retry-After": str(e.retry_after)}) from e
    service.check_input(content)
    media_part = media_meta = None
    meter = SpendMeter()  # создаётся до распознавания голоса: его стоимость тоже идёт в лимит пользователя
    if media is not None:
        token = current_meter.set(meter)
        try:
            media_part = await media_to_part(media, stt)
        except MediaError as e:
            raise HTTPException(status_code=e.status, detail=str(e)) from e
        finally:
            current_meter.reset(token)
        service.check_media_part(media_part)
        media_meta = {"mime": media.content_type, "size": media.size, "filename": media.filename}
    # Б4.4: модерация контента — текст пользователя + текст из документа/голоса
    document_text = media_part["text"] if media_part and media_part.get("type") == "text" else ""
    moderated_text = f"{content}\n{document_text}" if document_text else content
    try:
        await service.moderate_input(chat, moderated_text)
    except ModerationBlockedError as e:
        raise HTTPException(status_code=403, detail={"code": e.code, "categories": e.categories}) from e

    async def events() -> AsyncIterator[str]:
        token = current_meter.set(meter)  # все LLM-вызовы ради этого сообщения (condense + ответ) пишут сюда
        try:
            async for delta in service.send_message(chat_id, content, media_part, media_meta):
                yield sse_event("token", delta=delta)
        except ResponseReplaced as e:
            yield sse_event("replace", text=e.replacement)  # модерация вывода: клиент заменяет показанный текст
        except LLMError as e:
            log.warning("chat.stream_error", code=e.code)
            yield sse_event("error", code=e.code, message=e.message)
        finally:
            try:
                current_meter.reset(token)
            except ValueError:  # генератор закрыт из другого контекста (обрыв соединения) — сбрасывать нечего
                pass
            if limiter is not None:
                with anyio.CancelScope(shield=True):  # клиент отключился — траты всё равно записать
                    await limiter.add(chat.owner_external_id, meter)
            log.info("chat.spend", usd=round(meter.usd, 6), tokens=meter.tokens, llm_calls=meter.calls)
        if service.last_sources:  # Б5.5: источники — финальным событием, после текста
            yield sse_json({"type": "sources", "sources": service.last_sources}, event="sources")
        message_id = getattr(service, "last_assistant_message_id", None)  # для кнопок 👍/👎 в боте
        yield sse_event("done", message_id=str(message_id) if message_id else None)

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


@router.post(
    "/{chat_id}/messages/{message_id}/feedback",
    summary="👍/👎 на ответ ассистента",
    responses={404: {"description": "Сообщение не найдено в этом чате"}, 501: {"description": "Нужен Postgres"}},
)
async def message_feedback(chat_id: UUID, message_id: UUID, body: FeedbackIn, session: DbSessionDep) -> dict:
    try:
        await save_feedback(session, chat_id, message_id, body.value, body.sources)
    except FeedbackError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"status": "ok"}


@router.post(
    "/{chat_id}/system-message",
    summary="Служебное сообщение от backend (+ уведомление в Telegram)",
    description="Симуляция «фоновая задача завершилась». Только для внутренних сервисов: X-Internal-Token.",
    dependencies=[Depends(require_internal)],
    responses={401: {"description": "Нет или неверный X-Internal-Token"}, **NOT_FOUND},
)
async def system_message(chat_id: UUID, body: SystemMessageIn, service: ChatServiceDep, settings: SettingsDep) -> dict:
    chat = await service.get_chat(chat_id)
    await service.repo.append_message(chat_id, ChatMessage(chat_id=chat_id, role="assistant", content=body.text))
    notified = False
    if body.notify and chat.interface == "telegram":
        try:
            await notify_user(settings, int(chat.owner_external_id), body.text)
            notified = True
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            log.warning("notify.failed", error=type(e).__name__)
    return {"status": "ok", "notified": notified}
