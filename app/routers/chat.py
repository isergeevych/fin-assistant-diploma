from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.exceptions import LLMError
from app.core.sse import DONE, sse_json, sse_text
from app.deps.providers import LLMServiceDep
from app.schemas.chat import ChatRequest, ChatResponse, ErrorResponse

router = APIRouter(prefix="/chat", tags=["chat"])
log = structlog.get_logger("chat")

ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "Ввод отклонён фильтром безопасности (input_rejected)"},
    422: {"model": ErrorResponse, "description": "Ошибка валидации запроса"},
    429: {"model": ErrorResponse, "description": "Провайдер LLM ограничил частоту запросов"},
    502: {"model": ErrorResponse, "description": "Провайдер LLM недоступен или ошибка авторизации"},
    504: {"model": ErrorResponse, "description": "Провайдер LLM не ответил вовремя"},
}


@router.post(
    "",
    response_model=ChatResponse,
    summary="Ответ LLM целиком (с кешем)",
    description="Синхронный ответ. Одинаковые запросы в течение TTL возвращаются из Redis с `cached: true`.",
    responses={200: {"description": "Ответ модели"}, **ERROR_RESPONSES},
)
async def chat(req: ChatRequest, service: LLMServiceDep) -> ChatResponse:
    return await service.complete(req)


@router.post(
    "/stream",
    summary="Стриминг ответа через SSE",
    description=(
        "Ответ по мере генерации: кадры `data: <текст>` (многострочный текст — несколькими строками `data:`), "
        "затем `data: {\"usage\": {...}}` и `data: [DONE]`. Ошибка после начала стрима приходит кадром "
        "`data: {\"error\": {...}}`."
    ),
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE-поток"}, **ERROR_RESPONSES},
)
async def chat_stream(req: ChatRequest, service: LLMServiceDep) -> StreamingResponse:
    # Проверка входа ДО отправки заголовков: отказ должен прийти честным HTTP 400, а не 200 с ошибкой в потоке.
    service.check_input(req)

    async def events() -> AsyncIterator[str]:
        try:
            async for delta in service.stream(req):
                if delta.content is not None:
                    yield sse_text(delta.content)
                elif delta.usage is not None:
                    yield sse_json({"usage": delta.usage.model_dump()})
        except LLMError as e:
            # Заголовки (200) уже отправлены — сообщаем об ошибке кадром внутри потока.
            log.warning("chat.stream_error", code=e.code)
            yield sse_json({"error": {"code": e.code, "message": e.message}})
        yield DONE

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # nginx не должен буферизовать SSE
    )
