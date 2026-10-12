"""Б4.2 — сценарий /ask: FSMContext — настоящий (MemoryStorage), Telegram-объекты — моки."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import fsm
from bot.states import AskFlow


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=42, user_id=42))


def callback(data: str) -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


async def test_ask_then_topic_moves_to_waiting_for_question(state):
    message = MagicMock()
    message.answer = AsyncMock()
    await fsm.cmd_ask(message, state)
    assert await state.get_state() == AskFlow.waiting_for_topic.state

    await fsm.on_topic(callback("topic:taxes"), state)
    assert await state.get_state() == AskFlow.waiting_for_question.state
    assert (await state.get_data())["topic"] == "taxes"


async def test_cancel_button_clears_state(state):
    await state.set_state(AskFlow.waiting_for_topic)
    await fsm.on_topic(callback("topic:cancel"), state)
    assert await state.get_state() is None


async def test_forged_topic_is_ignored(state):
    await state.set_state(AskFlow.waiting_for_topic)
    await fsm.on_topic(callback("topic:<script>"), state)
    assert await state.get_state() == AskFlow.waiting_for_topic.state  # не сдвинулись
    assert "topic" not in await state.get_data()


async def test_question_builds_prompt_and_clears_state(state, monkeypatch):
    await state.set_state(AskFlow.waiting_for_question)
    await state.update_data(topic="credit")
    sent = {}

    async def fake_ask(message, backend, text, settings):
        sent["text"] = text

    monkeypatch.setattr(fsm, "ask_backend", fake_ask)
    message = MagicMock()
    message.text = "Как досрочно погасить ипотеку?"
    await fsm.on_question(message, state, backend=MagicMock(), settings=MagicMock())
    assert sent["text"] == "Тема: Кредиты и вклады. Вопрос: Как досрочно погасить ипотеку?"
    assert await state.get_state() is None
