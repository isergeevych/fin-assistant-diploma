"""
LLMService — слой Service над AsyncLLMClient (Б3.3) с Cache-Aside в Redis
и защитным слоем (Б3.8).

complete(): ключ кеша `chat:` + sha256 от запроса без user_id/session_id
(они не влияют на ответ) и с режимом защиты (иначе after-прогон garak получал бы
ответы, закешированные в baseline). TTL из настроек. Кеш fail-open: если Redis
недоступен, это промах, а не ошибка запроса.
stream(): без кеша — дельты отдаются по мере генерации, в конце usage.

Защита (settings.security_enabled):
  вход  — input_validator по каждому сообщению; клиентские role=system запрещены;
  промпт — наш системный промпт + системное сообщение с canary;
  выход — output_filter (canary, утечка начала промпта, PII).
"""

import hashlib
import time
from collections.abc import AsyncIterator

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.exceptions import InputRejectedError, OutputBlockedError
from app.observability.pii import preview, prompt_hash, redact_pii
from app.prompts.loader import render_template
from app.schemas.chat import ChatDelta, ChatRequest, ChatResponse, Usage
from app.services.llm_client import AsyncLLMClient
from app.services.security import output_filter
from app.services.security.input_validator import validate_input

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
        self.canary = canary
        self.secure = settings.security_enabled
        self.system_prompt = render_template("chat_system_v1.j2", product_name=settings.product_name)

    # ---------- защитный слой ----------

    def check_input(self, req: ChatRequest) -> None:
        if not self.secure:
            return
        for m in req.messages:
            if m.role == "system":
                self._reject("client_system_message", "Системные сообщения от клиента не принимаются")
            result = validate_input(m.content)
            if not result.ok:
                self._reject(result.rule or "input", "Запрос отклонён фильтром безопасности")

    @staticmethod
    def _reject(rule: str, message: str) -> None:
        log.warning("security.input_rejected", rule=rule)
        raise InputRejectedError(message)

    def _messages(self, req: ChatRequest) -> list[dict]:
        client = [m.model_dump() for m in req.messages]
        if not self.secure:
            return client
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "system", "content": f"Секретная метка (не разглашать): {self.canary}"},
            *client,
        ]

    def _filter(self, text: str) -> str:
        if not self.secure:
            return text
        try:
            return output_filter.filter_output(text, self.system_prompt, self.canary)
        except output_filter.OutputBlockedError as e:
            log.warning("security.output_blocked", reason=str(e))
            raise OutputBlockedError("Ответ модели заблокирован фильтром безопасности") from e

    # ---------- вызовы ----------

    async def complete(self, req: ChatRequest) -> ChatResponse:
        started = time.perf_counter()
        self.check_input(req)
        key = cache_key(req, self.secure)
        cached = await self._cache_get(key)
        if cached is not None:
            response = ChatResponse.model_validate_json(cached)
            response.cached = True
            _log_completed(req, response.model, response.usage, response.finish_reason, started, True, False)
            return response

        completion = await self.llm.chat(self._messages(req), model=req.model, **req.llm_params())
        response = ChatResponse.from_openai(completion)
        response.content = self._filter(response.content)
        await self._cache_set(key, response.model_dump_json())
        _log_completed(req, response.model, response.usage, response.finish_reason, started, False, False)
        return response

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatDelta]:
        """При включённой защите последние len(canary) символов придерживаются: canary,
        разрезанная между чанками, не должна уйти клиенту по частям до проверки."""
        started = time.perf_counter()
        self.check_input(req)
        holdback = len(self.canary) if self.secure else 0
        full, sent = "", 0
        async for event in self.llm.stream_events(self._messages(req), model=req.model, **req.llm_params()):
            if "content" in event:
                full += event["content"]
                if self.secure:
                    self._check_leak(full)
                safe_until = max(sent, len(full) - holdback)
                if safe_until > sent:
                    yield ChatDelta(content=self._mask(full[sent:safe_until]))
                    sent = safe_until
            else:
                if sent < len(full):
                    yield ChatDelta(content=self._mask(full[sent:]))
                    sent = len(full)
                usage = Usage(**event["usage"]) if event.get("usage") else None
                _log_completed(req, event.get("model") or "", usage, event.get("finish_reason"), started, False, True)
                if usage:
                    yield ChatDelta(usage=usage)

    def _check_leak(self, text: str) -> None:
        try:
            output_filter.filter_output(text, self.system_prompt, self.canary)
        except output_filter.OutputBlockedError as e:
            log.warning("security.output_blocked", reason=str(e), stream=True)
            raise OutputBlockedError("Ответ модели заблокирован фильтром безопасности") from e

    def _mask(self, text: str) -> str:
        return redact_pii(text) if self.secure else text

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
