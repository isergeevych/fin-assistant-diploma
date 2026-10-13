"""
Сценарий «вопрос по теме»: кнопка ❓ (или /ask) → тема (inline-кнопки) → вопрос → ответ стримом.

callback_data приходит от клиента и может быть подделан модифицированным клиентом,
поэтому slug проверяется по белому списку TOPICS — неизвестная тема игнорируется.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import BotSettings
from bot.handlers.common import ask_backend
from bot.keyboards.inline import CANCEL, TOPICS, cancel_kb, topics_kb
from bot.keyboards.reply import BTN_ASK
from bot.services.backend_client import BackendClient
from bot.states import AskFlow

router = Router(name="fsm")


@router.message(Command("ask"))
@router.message(F.text == BTN_ASK)
async def cmd_ask(message: Message, state: FSMContext) -> None:
    await state.set_state(AskFlow.waiting_for_topic)
    await message.answer("О чём вопрос?", reply_markup=topics_kb(), parse_mode=None)


@router.callback_query(AskFlow.waiting_for_topic, F.data.startswith("topic:"))
async def on_topic(callback: CallbackQuery, state: FSMContext) -> None:
    slug = callback.data.split(":", 1)[1]
    if slug == CANCEL:
        await state.clear()
        await callback.message.edit_text("Отменено.")
        await callback.answer()
        return
    if slug not in TOPICS:
        await callback.answer("Неизвестная тема", show_alert=False)
        return
    await state.update_data(topic=slug)
    await state.set_state(AskFlow.waiting_for_question)
    await callback.message.edit_text(f"Тема: {TOPICS[slug]}. Напишите вопрос одним сообщением.",
                                     reply_markup=cancel_kb())
    await callback.answer()


@router.callback_query(F.data == "ask:cancel")
async def on_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено. Можно просто написать вопрос — тему я пойму сам.")
    await callback.answer()


@router.message(AskFlow.waiting_for_question, F.text & ~F.text.startswith("/"))
async def on_question(message: Message, state: FSMContext, backend: BackendClient, settings: BotSettings) -> None:
    data = await state.get_data()
    topic = TOPICS.get(data.get("topic", ""), "общая")
    await state.clear()
    await ask_backend(message, backend, f"Тема: {topic}. Вопрос: {message.text}", settings)
