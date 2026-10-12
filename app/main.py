"""
Точка входа FastAPI: `uvicorn app.main:app --reload`.

lifespan создаёт клиентов (AsyncLLMClient с AsyncOpenAI внутри и Redis) один раз
на процесс и закрывает их на shutdown. Middleware ставит request_id и пишет
одну строку лога на запрос. Доменные ошибки LLM превращаются в HTTP 429/504/502
с телом {"error": {...}} — без traceback наружу.
"""

import asyncio
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin import routes as admin_routes
from app.chat import routes as chat_routes
from app.chat.service import ChatNotFoundError
from app.core.config import get_settings
from app.core.exceptions import LLMError
from app.observability.logging import setup_logging
from app.observability.tracing import setup_tracing
from app.routers import chat, documents, health, models, rag
from app.services.llm_client import AsyncLLMClient

# request_id от клиента принимаем только «безопасной» формы — иначе через заголовок
# можно было бы внедрить переводы строк и мусор в логи.
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
http_log = structlog.get_logger("llm-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, log_file=settings.log_dir / "app.jsonl")
    setup_tracing()  # до создания клиентов OpenAI — иначе патч инструментора их не затронет
    # Б3.8: canary генерируется заново при каждом старте и живёт только в памяти процесса
    app.state.canary = f"CANARY_{secrets.token_hex(4)}"
    app.state.llm = AsyncLLMClient(settings.llm)
    # Б5.3: RAG-индекс — один раз на старте. Недоступность Qdrant/эмбеддингов не должна ронять весь
    # сервис: /rag/query ответит 503, остальное работает (graceful degradation).
    app.state.rag = None
    if settings.rag_enabled:
        try:
            from app.services.rag import RAGService

            app.state.rag = await asyncio.to_thread(lambda: RAGService(settings).build())
        except Exception as e:  # noqa: BLE001
            structlog.get_logger("rag").warning("rag.unavailable", error=f"{type(e).__name__}: {e}"[:300])
    # Redis создаётся лениво: соединение откроется на первом запросе, поэтому
    # приложение стартует и /health работает, даже если Redis сейчас недоступен.
    app.state.redis = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
    )
    # Б4.1: Postgres — только если история хранится в нём (CHAT_REPOSITORY=postgres)
    engine = None
    if settings.chat_repository == "postgres":
        engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=5, pool_pre_ping=True)
        app.state.db_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    yield
    await app.state.llm.aclose()
    await app.state.redis.aclose()
    if engine is not None:
        await engine.dispose()


app = FastAPI(
    title="Финпомощник — LLM API",
    version="0.3.4",
    description="HTTP-сервис над LLM: синхронный ответ с кешем, SSE-стрим, каталог моделей.",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex[:12]
    user_id = request.headers.get("x-user-id", "")
    request.state.request_id = request_id
    # Контекст запроса — в contextvars: его подхватит каждая строка лога в этой цепочке,
    # включая llm_request_completed в сервисе. Очистка — чтобы не протёк в следующий запрос.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        user_id=user_id if REQUEST_ID_RE.fullmatch(user_id) else None,
        path=request.url.path,
        method=request.method,
    )
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        http_log.info("http_request", status=status, duration_ms=round((time.perf_counter() - started) * 1000, 1))
    response.headers["x-request-id"] = request_id
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,  # ["*"] + credentials недопустимо; куки нам не нужны
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in err["loc"] if p != "body"), "message": err["msg"]}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": "Некорректный запрос", "details": details}},
    )


@app.exception_handler(ChatNotFoundError)
async def chat_not_found_handler(request: Request, exc: ChatNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": {"code": "chat_not_found", "message": "Чат не найден"}})


app.include_router(health.router)
app.include_router(models.router)
app.include_router(chat.router)
app.include_router(rag.router)
app.include_router(documents.router)
# admin — РАНЬШЕ чат-роутера: иначе /chats/admin/* совпало бы с /chats/{chat_id}
app.include_router(admin_routes.router)
app.include_router(admin_routes.internal)
app.include_router(chat_routes.router)
