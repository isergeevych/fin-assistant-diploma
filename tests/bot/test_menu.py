"""Кнопки меню вместо команд: клавиатура, рассылка с подтверждением, кнопки не уходят в LLM как вопрос."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import admin, text
from bot.keyboards.reply import ADMIN_BUTTONS, BTN_STATS, MENU_BUTTONS, main_menu
from bot.states import BroadcastFlow


@pytest.fixture
def state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=42, user_id=42))


def _texts(markup) -> set[str]:
    return {b.text for row in markup.keyboard for b in row}


def test_admin_buttons_only_for_admins():
    assert not _texts(main_menu(False)) & set(ADMIN_BUTTONS)
    assert set(ADMIN_BUTTONS) <= _texts(main_menu(True))
    assert main_menu(False).is_persistent and main_menu(False).resize_keyboard


async def test_admin_filter_works_for_button_clicks():
    settings = SimpleNamespace(bot_admin_ids=[42])
    assert await admin.IsAdmin()(SimpleNamespace(from_user=SimpleNamespace(id=42)), settings) is True
    assert await admin.IsAdmin()(SimpleNamespace(from_user=SimpleNamespace(id=7)), settings) is False


def _message(text_: str = "") -> MagicMock:
    m = MagicMock()
    m.text = text_
    m.answer = AsyncMock()
    return m


def _callback(data: str) -> MagicMock:
    cb = MagicMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


async def test_broadcast_needs_confirmation(state):
    backend = MagicMock()
    backend.admin_broadcast = AsyncMock(return_value={"id": 7})
    settings = SimpleNamespace(admin_token=None)

    await admin.btn_broadcast(_message(), state)
    assert await state.get_state() == BroadcastFlow.waiting_for_text.state
    await admin.on_broadcast_text(_message("Завтра техработы с 2 до 4"), state)
    assert await state.get_state() == BroadcastFlow.confirming.state
    backend.admin_broadcast.assert_not_called()  # до нажатия «Отправить всем» ничего не ушло

    await admin.on_broadcast_send(_callback("bc:send"), state, backend, settings)
    backend.admin_broadcast.assert_awaited_once_with("Завтра техработы с 2 до 4", "")
    assert await state.get_state() is None


async def test_broadcast_cancel_sends_nothing(state):
    await admin.btn_broadcast(_message(), state)
    await admin.on_broadcast_text(_message("текст"), state)
    cb = _callback("bc:cancel")
    await admin.on_broadcast_cancel(cb, state)
    assert await state.get_state() is None
    assert "отменена" in cb.message.edit_text.await_args.args[0]


async def test_too_long_broadcast_is_not_previewed(state):
    await admin.btn_broadcast(_message(), state)
    await admin.on_broadcast_text(_message("x" * (admin.MAX_BROADCAST + 1)), state)
    assert await state.get_state() == BroadcastFlow.waiting_for_text.state


async def test_menu_button_text_is_not_sent_to_llm_as_question():
    # реальные фильтры хендлера «любой текст»: кнопка (например, админская от не-админа) не проходит, вопрос — проходит
    handler = text.router.message.handlers[0]
    passed_button, _ = await handler.check(SimpleNamespace(text=BTN_STATS), raw_state=None)
    passed_question, _ = await handler.check(SimpleNamespace(text="Как получить вычет?"), raw_state=None)
    assert passed_button is False and passed_question is True
