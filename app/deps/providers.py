"""
DI-провайдеры FastAPI. Клиенты создаются в lifespan и лежат в app.state —
никаких глобальных клиентов на уровне модулей.
"""

from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.services.llm import LLMService
from app.services.llm_client import AsyncLLMClient


def get_openai(request: Request) -> AsyncLLMClient:
    """AsyncLLMClient держит внутри AsyncOpenAI-клиенты всех провайдеров цепочки."""
    return request.app.state.llm


def get_cache(request: Request) -> Redis:
    return request.app.state.redis


SettingsDep = Annotated[Settings, Depends(get_settings)]
LLMClientDep = Annotated[AsyncLLMClient, Depends(get_openai)]
CacheDep = Annotated[Redis, Depends(get_cache)]


def get_llm_service(llm: LLMClientDep, cache: CacheDep, settings: SettingsDep) -> LLMService:
    return LLMService(llm, cache, settings)


LLMServiceDep = Annotated[LLMService, Depends(get_llm_service)]
