"""
Точка входа FastAPI: `uvicorn app.main:app --reload`.

lifespan создаёт клиентов (AsyncLLMClient с AsyncOpenAI внутри и Redis) один раз
на процесс и закрывает их на shutdown. Middleware ставит request_id и пишет
одну строку лога на запрос. Доменные ошибки LLM превращаются в HTTP 429/504/502
с телом {"error": {...}} — без traceback наружу.
"""

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.exceptions import LLMError
from app.core.logging import setup_json_logging
from app.routers import chat, health, models
from app.services.llm_client import AsyncLLMClient

# request_id от клиента принимаем только «безопасной» формы — иначе через заголовок
# можно было бы внедрить переводы строк и мусор в логи.
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
http_log = logging.getLogger("llm-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_json_logging(settings.log_dir, "app.jsonl", getattr(logging, settings.log_level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    app.state.llm = AsyncLLMClient(settings.llm)
    # Redis создаётся лениво: соединение откроется на первом запросе, поэтому
    # приложение стартует и /health работает, даже если Redis сейчас недоступен.
    app.state.redis = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
    )
    yield
    await app.state.llm.aclose()
    await app.state.redis.aclose()


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
    request.state.request_id = request_id
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        http_log.info("http.request", extra={"fields": {
            "request_id": request_id, "method": request.method, "path": request.url.path,
            "status": status, "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }})
    response.headers["X-Request-ID"] = request_id
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


app.include_router(health.router)
app.include_router(models.router)
app.include_router(chat.router)
