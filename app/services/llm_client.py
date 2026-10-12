"""
Б3.3 — асинхронный LLM-клиент.

AsyncLLMClient поверх AsyncOpenAI (все провайдеры OpenAI-совместимые):
  - complete(prompt) / chat(messages) — один вызов с fallback-цепочкой ADR-002:
    primary (OpenRouter gpt-5.4-mini) → fallback (OpenRouter claude-haiku-4.5) → local (Ollama);
  - batch_chat(prompts) — пачка через asyncio.gather(return_exceptions=True):
    упавший запрос возвращается как Exception на своей позиции, батч не падает;
  - batch_chat_strict(prompts) — «всё или ничего» через TaskGroup + except*;
  - stream_chat(prompt) — async-генератор дельт; usage логируется после стрима.

Надёжность, снаружи внутрь:
  Semaphore (атрибут экземпляра, Bulkhead) → circuit breaker провайдера →
  asyncio.timeout(call_timeout) на попытку → SDK: timeout=30 с на HTTP-запрос
  и max_retries=3 (экспонента + Retry-After на 429/5xx/таймаутах).

Почему retry средствами SDK, а не tenacity: SDK уже умеет экспоненту с jitter
и читает Retry-After; второй слой ретраев поверх умножил бы число запросов.

Почему asyncio.timeout на попытку, а не на весь complete: при общем
дедлайне 15 с зависший primary съел бы всё время, и до fallback дело бы
не дошло. Худший случай — call_timeout × число провайдеров.

Внутри async-кода нет синхронных клиентов, requests и time.sleep.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import openai
import structlog
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from app.core.config import LLMSettings
from app.core.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)

log = structlog.get_logger("llm")


@dataclass
class CircuitBreaker:
    """closed → (fail_max неудач подряд) → open на reset_timeout → half-open: одна пробная попытка."""

    fail_max: int
    reset_timeout: float
    failures: int = 0
    opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if time.monotonic() - self.opened_at >= self.reset_timeout else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.fail_max:
            self.opened_at = time.monotonic()


@dataclass
class ProviderSlot:
    name: str
    model: str
    client: AsyncOpenAI
    breaker: CircuitBreaker = field(repr=False)


class LLMBadRequestError(LLMError):
    """Ошибка в самом запросе (400/404/422): у другого провайдера она повторится — fallback не нужен."""

    code = "llm_bad_request"


def _to_domain(exc: BaseException) -> LLMError:
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError("Провайдер LLM ограничил частоту запросов")
    if isinstance(exc, (openai.APITimeoutError, TimeoutError)):
        return LLMTimeoutError("Провайдер LLM не ответил вовремя")
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return LLMAuthError("Ошибка авторизации у провайдера LLM")
    if isinstance(exc, (openai.BadRequestError, openai.NotFoundError, openai.UnprocessableEntityError)):
        return LLMBadRequestError(f"Некорректный запрос к LLM: {getattr(exc, 'message', exc)}")
    return LLMUnavailableError(f"Провайдер LLM недоступен: {type(exc).__name__}")


class AsyncLLMClient:
    def __init__(self, settings: LLMSettings, providers: list[ProviderSlot] | None = None):
        self.settings = settings
        self.call_timeout = settings.call_timeout
        self.max_concurrency = settings.max_concurrency
        # Semaphore создаётся ОДИН раз и живёт в экземпляре: ограничивает все вызовы
        # этого клиента (complete, batch, stream) — это и есть Bulkhead.
        self._sem = asyncio.Semaphore(settings.max_concurrency)
        self.providers = providers if providers is not None else self._build_providers(settings)

    @staticmethod
    def _build_providers(s: LLMSettings) -> list[ProviderSlot]:
        def slot(name: str, model: str, base_url: str, api_key: str) -> ProviderSlot:
            client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=s.request_timeout, max_retries=s.max_retries)
            return ProviderSlot(name, model, client, CircuitBreaker(s.cb_fail_max, s.cb_reset_timeout))

        key = s.api_key.get_secret_value()
        slots = [slot("primary", s.default_model, s.base_url, key)]
        if s.fallback_model:
            slots.append(slot("fallback", s.fallback_model, s.base_url, key))
        if s.local_base_url:
            slots.append(slot("local", s.local_model, s.local_base_url, "ollama"))
        return slots

    async def aclose(self) -> None:
        for p in self.providers:
            await p.client.close()

    # ---------- одиночный вызов ----------

    def _model_for(self, p: ProviderSlot, model: str | None) -> str:
        # Явно запрошенная модель применяется только к primary: у fallback своя модель другого вендора.
        return model if (model and p is self.providers[0]) else p.model

    async def chat(self, messages: list[dict], model: str | None = None, **params) -> ChatCompletion:
        """Полный ответ SDK от первого доступного провайдера цепочки."""
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        errors: list[LLMError] = []
        async with self._sem:
            for p in self.providers:
                if not p.breaker.allow():
                    log.warning("llm.circuit_open", provider=p.name, model=p.model)
                    continue
                target = self._model_for(p, model)
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(self.call_timeout):
                        response = await p.client.chat.completions.create(model=target, messages=messages, **params)
                except Exception as exc:  # noqa: BLE001 — классифицируем ниже
                    domain = _to_domain(exc)
                    log.warning("llm.call", provider=p.name, model=target, prompt_chars=prompt_chars,
                                duration_ms=_ms(started), status="error", error=domain.code)
                    if isinstance(domain, LLMBadRequestError):
                        raise domain from exc
                    p.breaker.failure()
                    errors.append(domain)
                    continue
                p.breaker.success()
                usage = response.usage
                log.info("llm.call", provider=p.name, model=target, prompt_chars=prompt_chars,
                         duration_ms=_ms(started), status="ok",
                         finish_reason=response.choices[0].finish_reason,
                         total_tokens=usage.total_tokens if usage else None)
                return response
        raise _combine(errors)

    async def complete(self, prompt: str, **params) -> str:
        response = await self.chat([{"role": "user", "content": prompt}], **params)
        return response.choices[0].message.content or ""

    # ---------- пачки ----------

    async def batch_chat(self, prompts: list[str], concurrency: int | None = None, **params) -> list[str | Exception]:
        """Конкурентность ограничивает Semaphore экземпляра (задаётся в конструкторе через
        LLM__MAX_CONCURRENCY). Аргумент concurrency оставлен ради сигнатуры из задания и
        лишь проверяется: создавать новый Semaphore внутри метода нельзя."""
        if concurrency is not None and concurrency != self.max_concurrency:
            raise ValueError(
                f"concurrency={concurrency}, а клиент создан с max_concurrency={self.max_concurrency}: "
                "создайте клиент с нужным лимитом"
            )
        coros = [self.complete(p, **params) for p in prompts]
        return await asyncio.gather(*coros, return_exceptions=True)

    async def batch_chat_strict(self, prompts: list[str], models: list[str] | None = None, **params) -> list[str]:
        """«Все ответы или ничего»: первая же ошибка отменяет остальные задачи TaskGroup,
        наружу летит ExceptionGroup. models — для эксперимента с подменой модели."""
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._complete_with_model(p, (models or [None] * len(prompts))[i], **params))
                for i, p in enumerate(prompts)
            ]
        return [t.result() for t in tasks]

    async def _complete_with_model(self, prompt: str, model: str | None, **params) -> str:
        if model is None:
            return await self.complete(prompt, **params)
        p = self.providers[0]
        async with self._sem:
            try:
                response = await p.client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}], **params)
            except Exception as exc:  # noqa: BLE001
                raise _to_domain(exc) from exc
        return response.choices[0].message.content or ""

    # ---------- стрим ----------

    async def stream_chat(self, prompt: str | list[dict], **params) -> AsyncIterator[str]:
        """Только текстовые дельты (контракт задания Б3.3)."""
        async for event in self.stream_events(prompt, **params):
            if "content" in event:
                yield event["content"]

    async def stream_events(self, prompt: str | list[dict], model: str | None = None, **params) -> AsyncIterator[dict]:
        """События стрима: {"content": str} по мере генерации и финальное {"usage": {...}, "model": ...}.
        Fallback возможен только до первого токена: начатый стрим на другого провайдера
        не переключишь — пользователь уже видит текст."""
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        errors: list[LLMError] = []
        async with self._sem:
            for p in self.providers:
                if not p.breaker.allow():
                    continue
                target = self._model_for(p, model)
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(self.call_timeout):
                        stream = await p.client.chat.completions.create(
                            model=target, messages=messages, stream=True,
                            stream_options={"include_usage": True}, **params)
                        iterator = aiter(stream)
                        first = await anext(iterator)
                except StopAsyncIteration:
                    p.breaker.success()
                    return
                except Exception as exc:  # noqa: BLE001
                    domain = _to_domain(exc)
                    log.warning("llm.stream", provider=p.name, model=p.model, status="error", error=domain.code)
                    if isinstance(domain, LLMBadRequestError):
                        raise domain from exc
                    p.breaker.failure()
                    errors.append(domain)
                    continue

                p.breaker.success()
                ttft_ms, usage, finish_reason = _ms(started), None, None
                chunk = first
                while True:
                    if chunk.usage:  # финальный чанк с include_usage: choices пустой
                        usage = chunk.usage
                    if chunk.choices:
                        finish_reason = chunk.choices[0].finish_reason or finish_reason
                        delta = chunk.choices[0].delta.content
                        if delta:
                            yield {"content": delta}
                    try:
                        chunk = await anext(iterator)
                    except StopAsyncIteration:
                        break
                log.info("llm.stream", provider=p.name, model=target, status="ok", ttft_ms=ttft_ms,
                         duration_ms=_ms(started), finish_reason=finish_reason,
                         total_tokens=usage.total_tokens if usage else None,
                         prompt_tokens=usage.prompt_tokens if usage else None,
                         completion_tokens=usage.completion_tokens if usage else None)
                yield {
                    "usage": usage.model_dump(include={"prompt_tokens", "completion_tokens", "total_tokens"})
                    if usage else None,
                    "model": chunk.model or target,
                    "finish_reason": finish_reason,
                }
                return
        raise _combine(errors)


def _combine(errors: list[LLMError]) -> LLMError:
    if not errors:
        return LLMUnavailableError("Все провайдеры LLM временно отключены (circuit breaker открыт)")
    kinds = {type(e) for e in errors}
    return errors[0] if len(kinds) == 1 else LLMUnavailableError("Все провайдеры LLM недоступны")


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
