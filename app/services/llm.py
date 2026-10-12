"""
LLMService — слой Service над AsyncLLMClient (Б3.3) с Cache-Aside в Redis.

complete(): ключ кеша `chat:` + sha256 от запроса без user_id/session_id
(они не влияют на ответ), TTL из настроек. Кеш fail-open: если Redis
недоступен, это промах, а не ошибка запроса.
stream(): без кеша — дельты отдаются по мере генерации, в конце usage.
"""

import hashlib
from collections.abc import AsyncIterator

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.schemas.chat import ChatDelta, ChatRequest, ChatResponse, Usage
from app.services.llm_client import AsyncLLMClient

log = structlog.get_logger("llm.service")


def cache_key(req: ChatRequest) -> str:
    payload = req.model_dump_json(exclude={"user_id", "session_id", "stream"})
    return "chat:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMService:
    def __init__(self, llm: AsyncLLMClient, cache: Redis | None, settings: Settings):
        self.llm = llm
        self.cache = cache
        self.settings = settings

    async def complete(self, req: ChatRequest) -> ChatResponse:
        key = cache_key(req)
        cached = await self._cache_get(key)
        if cached is not None:
            response = ChatResponse.model_validate_json(cached)
            response.cached = True
            log.info("cache.hit", key=key[:20])
            return response

        completion = await self.llm.chat(
            [m.model_dump() for m in req.messages], model=req.model, **req.llm_params()
        )
        response = ChatResponse.from_openai(completion)
        await self._cache_set(key, response.model_dump_json())
        return response

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatDelta]:
        async for event in self.llm.stream_events(
            [m.model_dump() for m in req.messages], model=req.model, **req.llm_params()
        ):
            if "content" in event:
                yield ChatDelta(content=event["content"])
            elif event.get("usage"):
                yield ChatDelta(usage=Usage(**event["usage"]))

    async def _cache_get(self, key: str) -> str | None:
        if self.cache is None:
            return None
        try:
            return await self.cache.get(key)
        except RedisError as e:
            log.warning("cache.unavailable", op="get", error=type(e).__name__)
            return None

    async def _cache_set(self, key: str, value: str) -> None:
        if self.cache is None:
            return
        try:
            await self.cache.setex(key, self.settings.cache_ttl_seconds, value)
        except RedisError as e:
            log.warning("cache.unavailable", op="set", error=type(e).__name__)
