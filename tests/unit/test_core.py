"""
Б3.7 — unit-тесты вокруг LLM. Без сети и API-ключей: LLM, Redis и HTTP мокаются.
Мокаем там, где объект используется (app.llm.client.*), а не где определён.

    pytest tests/unit/ -m "not llm"
"""

import json

import httpx2
import pytest
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from pydantic import ValidationError

from app.core.config import LLMSettings, Settings
from app.llm.client import run_tool_call
from app.llm.parsing import parse_json_response, parse_tool_calls
from app.llm.pricing import cost_usd
from app.prompts.loader import render_template
from app.schemas.chat import ChatRequest, Message
from app.services.llm import LLMService
from eval.check_thresholds import check


def completion(text: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "openai/gpt-5.4-mini",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    })


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(llm=LLMSettings(api_key="test"), log_dir=tmp_path)


# ---------- формирование промптов ----------

async def test_prompt_roles_order_system_then_user(mocker, settings):
    fake = mocker.MagicMock()
    fake.chat.completions.create = mocker.AsyncMock(return_value=completion("ok"))
    await run_tool_call("Сколько я потратил?", "tg:A", client=fake, settings=settings)
    messages = fake.chat.completions.create.call_args.kwargs["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == "Сколько я потратил?"


async def test_user_braces_are_not_evaluated_as_template(mocker, settings):
    """{...} и {{ ... }} от пользователя не должны исполняться ни f-string'ом, ни Jinja."""
    fake = mocker.MagicMock()
    fake.chat.completions.create = mocker.AsyncMock(return_value=completion("ok"))
    payload = "{{ 7*7 }} {settings.llm.api_key} {0.__class__}"
    await run_tool_call(payload, "tg:A", client=fake, settings=settings)
    messages = fake.chat.completions.create.call_args.kwargs["messages"]
    assert messages[1]["content"] == payload
    assert "49" not in messages[0]["content"]
    judge_prompt = render_template("judge_geval.j2", question=payload, expected_answer="x", answer=payload)
    assert "{{ 7*7 }}" in judge_prompt and "49" not in judge_prompt


# ---------- парсинг ответов ----------

def test_parse_json_in_markdown_fence():
    text = 'Вот оценка:\n```json\n{"reasoning": "ok", "correctness": 5}\n```\nГотово.'
    assert parse_json_response(text) == {"reasoning": "ok", "correctness": 5}


def test_parse_json_without_fence_with_noise():
    assert parse_json_response('Ответ: {"a": 1, "b": [1, 2]} — конец')["b"] == [1, 2]


@pytest.mark.parametrize("bad", ["", "просто текст", '{"a": 1,,}', "```json\n[1, 2]\n```"])
def test_parse_malformed_json_raises_value_error(bad):
    with pytest.raises(ValueError):
        parse_json_response(bad)


def test_parse_tool_calls():
    msg = ChatCompletionMessage.model_validate({"role": "assistant", "content": None, "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": "get_exchange_rate", "arguments": '{"currency": "EUR", "date": null}'}},
    ]})
    assert parse_tool_calls(msg) == [("get_exchange_rate", {"currency": "EUR", "date": None})]
    broken = ChatCompletionMessage.model_validate({"role": "assistant", "content": None, "tool_calls": [
        {"id": "2", "type": "function", "function": {"name": "x", "arguments": "{oops"}},
    ]})
    with pytest.raises(ValueError):
        parse_tool_calls(broken)


# ---------- валидация схем ----------

def test_chat_request_rejects_empty_and_too_long():
    with pytest.raises(ValidationError):
        ChatRequest(messages=[{"role": "user", "content": ""}])
    with pytest.raises(ValidationError):
        ChatRequest(messages=[{"role": "user", "content": "x" * 8001}])
    with pytest.raises(ValidationError):
        ChatRequest(messages=[{"role": "user", "content": "hi"}] * 51)
    with pytest.raises(ValidationError):
        ChatRequest(messages=[{"role": "tool", "content": "hi"}])


def test_message_repr_masks_pii():
    msg = Message(role="user", content="почта ivan@mail.ru, карта 4111 1111 1111 1111")
    text = repr(msg) + str(msg)
    assert "ivan@mail.ru" not in text and "4111" not in text
    assert msg.content.startswith("почта ivan@")  # само значение не испорчено


# ---------- бизнес-логика ----------

def test_cost_from_usage():
    # gpt-5.4-mini: $0.75 / $4.50 за 1M → 1000 in + 500 out = 0.00075 + 0.00225
    assert cost_usd("openai/gpt-5.4-mini", 1000, 500) == pytest.approx(0.003)
    assert cost_usd("gemma3:4b", 1000, 500) == 0.0


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value


async def test_llm_service_cache_miss_then_hit(mocker, settings):
    llm = mocker.MagicMock()
    llm.chat = mocker.AsyncMock(return_value=completion("Подушка — это запас"))
    service = LLMService(llm, FakeRedis(), settings)
    req = ChatRequest(messages=[{"role": "user", "content": "Что такое подушка?"}], user_id="u1")
    first = await service.complete(req)
    second = await service.complete(req.model_copy(update={"user_id": "u2"}))  # user_id не влияет на ключ
    assert first.cached is False and second.cached is True
    assert second.content == first.content
    assert llm.chat.await_count == 1


async def test_sdk_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx2.Response(429, json={"error": {"message": "rate limit"}}, headers={"retry-after": "0"})
        return httpx2.Response(200, json=completion("ok").model_dump())

    client = AsyncOpenAI(api_key="t", base_url="https://example.test/v1", max_retries=2,
                         http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    response = await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    assert response.choices[0].message.content == "ok"
    assert calls["n"] == 2


def test_check_thresholds_reports_each_failure():
    thresholds = {"correctness_avg": {"min": 4.0}, "min_correctness": {"min": 2.0}, "must_not_violations": {"max": 0}}
    assert check({"correctness_avg": 4.3, "min_correctness": 3, "must_not_violations": 0}, thresholds) == []
    failures = check({"correctness_avg": 3.1, "min_correctness": 1, "must_not_violations": 2}, thresholds)
    assert len(failures) == 3
