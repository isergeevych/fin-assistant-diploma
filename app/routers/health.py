"""
Две пробы для оркестратора (Б3.5):
- /health — liveness: процесс жив. Не трогает внешние сервисы и отвечает 200 всегда,
  чтобы оркестратор не перезапускал контейнер из-за временно лежащего Redis.
- /ready — readiness: готов ли принимать трафик. Пингует Redis с таймаутом;
  при недоступности — 503, и балансировщик перестаёт слать запросы на этот экземпляр.
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.deps.providers import CacheDep

router = APIRouter(tags=["health"])
READY_TIMEOUT_SEC = 1.5


@router.get("/health", summary="Liveness: процесс жив", description="Не зависит от Redis и LLM — отвечает 200 всегда.")
async def health() -> dict:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness: зависимости доступны",
    responses={200: {"description": "Redis доступен"}, 503: {"description": "Redis недоступен"}},
)
async def ready(redis: CacheDep) -> JSONResponse:
    try:
        async with asyncio.timeout(READY_TIMEOUT_SEC):
            await redis.ping()
    except (RedisError, TimeoutError, OSError):
        return JSONResponse(status_code=503, content={"status": "degraded", "redis": "down"})
    return JSONResponse(status_code=200, content={"status": "ok", "redis": "up"})
