"""Б3.3 — тесты AsyncLLMClient на фейковых провайдерах (без сети и ключей)."""

import asyncio

try:  # openai SDK 3.x работает поверх httpx2, 2.x (его требует LlamaIndex) — поверх httpx
    import httpx2
except ImportError:
    import httpx as httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from app.core.config import LLMSettings
from app.core.exceptions import LLMAuthError, LLMUnavailableError
from app.services.llm_client import AsyncLLMClient, CircuitBreaker, ProviderSlot, _to_domain


def completion(text: str) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "fake",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    })


def api_error(cls, status: int):
    request = httpx2.Request("POST", "https://x/chat/completions")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


class FakeProvider:
    """Имитирует AsyncOpenAI: либо отвечает текстом (с задержкой), либо бросает ошибку."""

    def __init__(self, reply: str | None = None, error: Exception | None = None, delay: float = 0.0):
        self.reply, self.error, self.delay = reply, error, delay
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            return completion(self.reply.format(**{"prompt": kwargs["messages"][-1]["content"]}))
        finally:
            self.in_flight -= 1

    async def close(self):
        pass


def make_client(*fakes: FakeProvider, concurrency: int = 5, fail_max: int = 5) -> AsyncLLMClient:
    settings = LLMSettings(api_key="test", max_concurrency=concurrency, call_timeout=2.0)
    slots = [ProviderSlot(f"p{i}", "m", fake, CircuitBreaker(fail_max, 60)) for i, fake in enumerate(fakes)]
    return AsyncLLMClient(settings, providers=slots)


async def test_fallback_to_next_provider_on_error():
    primary = FakeProvider(error=api_error(openai.InternalServerError, 500))
    fallback = FakeProvider(reply="ok from fallback")
    client = make_client(primary, fallback)
    assert await client.complete("hi") == "ok from fallback"
    assert primary.calls == 1 and fallback.calls == 1


async def test_same_error_everywhere_maps_to_domain_auth_error():
    client = make_client(FakeProvider(error=api_error(openai.AuthenticationError, 401)),
                         FakeProvider(error=api_error(openai.AuthenticationError, 401)))
    with pytest.raises(LLMAuthError):
        await client.complete("hi")


async def test_circuit_breaker_skips_provider_after_fail_max():
    primary = FakeProvider(error=api_error(openai.InternalServerError, 503))
    fallback = FakeProvider(reply="ok")
    client = make_client(primary, fallback, fail_max=2)
    for _ in range(4):
        assert await client.complete("hi") == "ok"
    assert primary.calls == 2  # после 2 неудач breaker открыт — primary больше не дёргаем


async def test_all_breakers_open_raises_unavailable():
    client = make_client(FakeProvider(error=api_error(openai.InternalServerError, 503)), fail_max=1)
    with pytest.raises(Exception):
        await client.complete("hi")
    with pytest.raises(LLMUnavailableError):
        await client.complete("hi")


async def test_batch_chat_returns_exceptions_in_place():
    class Flaky(FakeProvider):
        async def create(self, **kwargs):
            if "3" in kwargs["messages"][-1]["content"]:
                raise api_error(openai.InternalServerError, 500)
            return await super().create(**kwargs)

    client = make_client(Flaky(reply="ans:{prompt}"))
    results = await client.batch_chat([f"q{i}" for i in range(1, 6)])
    assert results[0] == "ans:q1" and results[4] == "ans:q5"
    assert isinstance(results[2], Exception)  # упавший запрос — на своей позиции, батч не упал


async def test_semaphore_limits_concurrency():
    fake = FakeProvider(reply="ok", delay=0.05)
    client = make_client(fake, concurrency=3)
    await client.batch_chat([f"q{i}" for i in range(12)])
    assert fake.max_in_flight == 3


async def test_batch_concurrency_must_match_instance_semaphore():
    client = make_client(FakeProvider(reply="ok"), concurrency=5)
    with pytest.raises(ValueError):
        await client.batch_chat(["q"], concurrency=10)


async def test_timeout_triggers_fallback():
    slow = FakeProvider(reply="slow", delay=5)
    fast = FakeProvider(reply="fast")
    client = make_client(slow, fast)  # call_timeout=2 с
    assert await client.complete("hi") == "fast"


async def test_batch_strict_raises_exception_group():
    class Flaky(FakeProvider):
        async def create(self, **kwargs):
            if kwargs["messages"][-1]["content"] == "bad":
                raise api_error(openai.InternalServerError, 500)
            return await super().create(**kwargs)

    client = make_client(Flaky(reply="ok"))
    with pytest.raises(ExceptionGroup):
        await client.batch_chat_strict(["a", "bad", "c"])


def test_bad_request_message_hides_provider_account_data():
    # Так OpenRouter отвечает на неизвестную модель: user_id аккаунта — рядом с текстом ошибки
    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx2.Response(400, request=request)
    exc = openai.BadRequestError(
        "Error code: 400 - {'error': {'message': 'x is not a valid model ID', 'code': 400}, 'user_id': 'user_SECRET'}",
        response=response, body={"message": "x is not a valid model ID", "code": 400})
    err = _to_domain(exc)
    assert "x is not a valid model ID" in err.message and "user_SECRET" not in err.message
