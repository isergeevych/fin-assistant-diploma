"""Б4.1 — ChatService: контекст, токен-бюджет, сохранение ответа (в т. ч. при обрыве стрима)."""

import pytest

from app.chat.context import count_tokens, fit_to_budget, sliding_window
from app.chat.domain import ChatMessage
from app.chat.repositories.json_repo import JsonChatRepository
from app.chat.service import ChatNotFoundError, ChatService
from app.core.config import LLMSettings, Settings
from app.services.security.guard import SecurityGuard


class ScriptedLLM:
    """Фейковая модель: отдаёт заданный ответ по кусочкам и запоминает присланный контекст."""

    def __init__(self, reply: str = "ok", fail_after: int | None = None):
        self.reply, self.fail_after = reply, fail_after
        self.calls: list[list[dict]] = []

    async def stream_events(self, messages, **params):
        self.calls.append(messages)
        for i, ch in enumerate(self.reply.split(" ")):
            if self.fail_after is not None and i >= self.fail_after:
                raise ConnectionError("обрыв стрима")
            yield {"content": ch + " "}
        yield {"usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}, "model": "fake"}


@pytest.fixture
def make_service(tmp_path):
    def _make(llm, window=10, security=False, context_tokens=16000):
        settings = Settings(llm=LLMSettings(api_key="t"), chat_storage_dir=tmp_path, chat_context_window=window,
                            llm_context_tokens=context_tokens, security_enabled=security)
        guard = SecurityGuard(security, "Ты — Финпомощник.", "CANARY_test1234")
        return ChatService(JsonChatRepository(tmp_path), llm, settings, guard)
    return _make


async def collect(gen) -> str:
    return "".join([c async for c in gen])


async def test_history_is_sent_to_llm_on_second_message(make_service):
    llm = ScriptedLLM("Приятно познакомиться")
    service = make_service(llm)
    chat = await service.create_chat("tg:1", "cli")
    await collect(service.send_message(chat.id, "Привет, меня зовут Аня"))
    await collect(service.send_message(chat.id, "Как меня зовут?"))
    second_context = llm.calls[1]
    assert second_context[0]["role"] == "system"
    assert [m["content"].strip() for m in second_context[1:]][:2] == ["Привет, меня зовут Аня", "Приятно познакомиться"]
    assert second_context[-1] == {"role": "user", "content": "Как меня зовут?"}
    history = await service.list_messages(chat.id)
    assert [m.role for m in history] == ["user", "assistant", "user", "assistant"]


async def test_sliding_window_limits_history(make_service):
    llm = ScriptedLLM("ok")
    service = make_service(llm, window=3)
    chat = await service.create_chat("tg:1", "cli")
    for i in range(4):
        await collect(service.send_message(chat.id, f"q{i}"))
    non_system = [m for m in llm.calls[-1] if m["role"] != "system"]
    assert len(non_system) == 3 and non_system[-1]["content"] == "q3"


async def test_partial_answer_saved_when_stream_breaks(make_service):
    service = make_service(ScriptedLLM("раз два три четыре", fail_after=2))
    chat = await service.create_chat("tg:1", "cli")
    with pytest.raises(ConnectionError):
        await collect(service.send_message(chat.id, "считай"))
    history = await service.list_messages(chat.id)
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[1].content.strip() == "раз два"


async def test_clear_history(make_service):
    service = make_service(ScriptedLLM("ok"))
    chat = await service.create_chat("tg:1", "cli")
    await collect(service.send_message(chat.id, "q"))
    await service.clear_history(chat.id)
    assert await service.list_messages(chat.id) == []


async def test_unknown_chat_raises(make_service):
    from uuid import uuid4
    with pytest.raises(ChatNotFoundError):
        await collect(make_service(ScriptedLLM()).send_message(uuid4(), "q"))


async def test_reuse_existing_chat_is_idempotent(make_service):
    service = make_service(ScriptedLLM())
    a = await service.create_chat("tg:7", "telegram", reuse_existing=True)
    b = await service.create_chat("tg:7", "telegram", reuse_existing=True)
    assert a.id == b.id


def test_fit_to_budget_keeps_system_and_last_message():
    messages = [{"role": "system", "content": "s"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "слово " * 50} for i in range(10)
    ] + [{"role": "user", "content": "последний вопрос"}]
    fitted = fit_to_budget(messages, budget=200)
    assert fitted[0]["role"] == "system" and fitted[-1]["content"] == "последний вопрос"
    assert count_tokens(fitted) <= 200 or len(fitted) == 2
    assert len(fitted) < len(messages)


def test_sliding_window_drops_stored_system_messages():
    from uuid import uuid4
    cid = uuid4()
    history = [ChatMessage(chat_id=cid, role="system", content="x"), ChatMessage(chat_id=cid, role="user", content="q")]
    assert sliding_window(history, 10) == [{"role": "user", "content": "q"}]
