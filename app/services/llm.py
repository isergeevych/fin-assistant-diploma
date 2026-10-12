"""
LLMService — слой Service над AsyncLLMClient (Б3.3) с Cache-Aside в Redis.

complete(): ключ кеша `chat:` + sha256 от запроса без user_id/session_id
(они не влияют на ответ), TTL из настроек. Кеш fail-open: если Redis
недоступен, это промах, а не ошибка запроса.
stream(): без кеша — дельты отдаются по мере генерации, в конце usage.
"""

import hashlib
import time
from collections.abc import AsyncIterator

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.observability.pii import preview, prompt_hash
from app.schemas.chat import ChatDelta, ChatRequest, ChatResponse, Usage
from app.services.llm_client import AsyncLLMClient

log = structlog.get_logger("llm.service")


def _log_completed(req: ChatRequest, model: str, usage: Usage | None, finish_reason: str | None,
                   started: float, cached: bool, stream: bool) -> None:
    """Одна строка на LLM-запрос. Сырой промпт не пишется никогда — только hash и маскированный preview.
    request_id/user_id подтягиваются из contextvars, проставленных HTTP-middleware."""
    last_user = next((m.content for m in reversed(req.messages) if m.role == "user"), "")
    log.info(
        "llm_request_completed",
        model=model,
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
        latency_ms=round((time.perf_counter() - started) * 1000),
        finish_reason=finish_reason,
        cached=cached,
        stream=stream,
        prompt_hash=prompt_hash(last_user),
        prompt_preview=preview(last_user),
    )


def cache_key(req: ChatRequest) -> str:
    payload = req.model_dump_json(exclude={"user_id", "session_id", "stream"})
    return "chat:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMService:
    def __init__(self, llm: AsyncLLMClient, cache: Redis | None, settings: Settings):
        self.llm = llm
        self.cache = cache
        self.settings = settings

    async def complete(self, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        key = cache_key(req)
        cached = await self._cache_get(key)
        if cached is not None:
            response = ChatResponse.model_validate_json(cached)
            response.cached = True
            _log_completed(req, response.model, response.usage, response.finish_reason, started, True, False)
            return response

        completion = await self.llm.chat(
            [m.model_dump() for m in req.messages], model=req.model, **req.llm_params()
        )
        response = ChatResponse.from_openai(completion)
        await self._cache_set(key, response.model_dump_json())
        _log_completed(req, response.model, response.usage, response.finish_reason, started, False, False)
        return response

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatDelta]:
        started = time.perf_counter()
        async for event in self.llm.stream_events(
            [m.model_dump() for m in req.messages], model=req.model, **req.llm_params()
        ):
            if "content" in event:
                yield ChatDelta(content=event["content"])
            else:
                usage = Usage(**event["usage"]) if event.get("usage") else None
                _log_completed(req, event.get("model") or "", usage, event.get("finish_reason"), started, False, True)
                if usage:
                    yield ChatDelta(usage=usage)

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
