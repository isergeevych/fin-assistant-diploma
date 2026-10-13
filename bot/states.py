from aiogram.fsm.state import State, StatesGroup


class AskFlow(StatesGroup):
    waiting_for_topic = State()
    waiting_for_question = State()
    confirming = State()  # зарезервировано (опциональный шаг подтверждения)


class BroadcastFlow(StatesGroup):
    """Кнопка 📣: текст → предпросмотр → «Отправить всем» / «Отмена»."""

    waiting_for_text = State()
    confirming = State()
