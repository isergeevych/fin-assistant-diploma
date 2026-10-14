"""
LLMService — слой Service над AsyncLLMClient (Б3.3) с Cache-Aside в Redis
и защитным слоем (Б3.8, SecurityGuard).

complete(): ключ кеша `chat:` + sha256 от запроса без user_id/session_id
(они не влияют на ответ) и с режимом защиты (иначе after-прогон garak получал бы
ответы, закешированные в baseline). TTL из настроек. Кеш fail-open: если Redis
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
from app.prompts.loader import render_template
from app.schemas.chat import ChatDelta, ChatRequest, ChatResponse, Usage
from app.services.llm_client import AsyncLLMClient
from app.services.security.guard import SecurityGuard

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


def cache_key(req: ChatRequest, security_enabled: bool = False) -> str:
    payload = req.model_dump_json(exclude={"user_id", "session_id", "stream"})
    return f"chat:{'sec' if security_enabled else 'raw'}:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LLMService:
    def __init__(self, llm: AsyncLLMClient, cache: Redis | None, settings: Settings, canary: str = ""):
        self.llm = llm
        self.cache = cache
        self.settings = settings
        self.guard = SecurityGuard(
            settings.security_enabled,
            render_template("chat_system_v2.j2", product_name=settings.product_name),
            canary,
        )

    def check_input(self, req: ChatRequest) -> None:
        self.guard.check((m.role, m.content) for m in req.messages)

    async def complete(self, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        self.check_input(req)
        key = cache_key(req, self.guard.enabled)
        cached = await self._cache_get(key)
        if cached is not None:
            response = ChatResponse.model_validate_json(cached)
            response.cached = True
            _log_completed(req, response.model, response.usage, response.finish_reason, started, True, False)
            return response

        messages = self.guard.wrap([m.model_dump() for m in req.messages])
        completion = await self.llm.chat(messages, model=req.model, **req.llm_params())
        response = ChatResponse.from_openai(completion)
        response.content = self.guard.filter_text(response.content)
        await self._cache_set(key, response.model_dump_json())
        _log_completed(req, response.model, response.usage, response.finish_reason, started, False, False)
        return response

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatDelta]:
        started = time.perf_counter()
        self.check_input(req)
        final: dict = {}

        async def contents() -> AsyncIterator[str]:
            messages = self.guard.wrap([m.model_dump() for m in req.messages])
            async for event in self.llm.stream_events(messages, model=req.model, **req.llm_params()):
                if "content" in event:
                    yield event["content"]
                else:
                    final.update(event)

        async for delta in self.guard.filter_stream(contents()):
            yield ChatDelta(content=delta)
        usage = Usage(**final["usage"]) if final.get("usage") else None
        _log_completed(req, final.get("model") or "", usage, final.get("finish_reason"), started, False, True)
        if usage:
            yield ChatDelta(usage=usage)

    # ---------- кеш ----------

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
