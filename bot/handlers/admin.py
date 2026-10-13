"""
Админ-функции: кнопки 📊 Статистика, 👥 Пользователи, 📣 Рассылка (и старые /stats, /users, /broadcast).

Доступ — фильтр IsAdmin на уровне РОУТЕРА и для сообщений, и для нажатий inline-кнопок, а не if внутри
каждого хендлера: новый хендлер в этом роутере не сможет «забыть» проверку. Кнопки меню видят только админы,
но текст кнопки можно отправить и вручную — поэтому проверка здесь обязательна.
Для не-админа всё молча не срабатывает (не раскрываем, что функции существуют).
Рассылка необратима: текст → предпросмотр → «✅ Отправить всем» / «✖️ Отмена».
Данные backend форматируются в HTML, все значения экранируются (html.escape).
"""

import html

import httpx
from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import BotSettings
from bot.handlers.errors import user_message_for
from bot.keyboards.inline import broadcast_confirm_kb, cancel_kb
from bot.keyboards.reply import BTN_BROADCAST, BTN_STATS, BTN_USERS, MENU_BUTTONS
from bot.services.backend_client import BackendClient
from bot.states import BroadcastFlow

router = Router(name="admin")
MAX_BROADCAST = 1000


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, settings: BotSettings) -> bool:
        return event.from_user is not None and event.from_user.id in settings.bot_admin_ids


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _token(settings: BotSettings) -> str:
    return settings.admin_token.get_secret_value() if settings.admin_token else ""


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")  # 50 000, а не 50,000


def format_stats(s: dict) -> str:
    e = html.escape
    ratio = "—" if s.get("feedback_up_ratio") is None else f"{s['feedback_up_ratio']:.0%}"
    latency = "—" if s.get("avg_latency_ms") is None else f"{s['avg_latency_ms']:.0f} мс"
    lines = [
        "<b>Статистика за 24 часа</b>",
        f"Сообщений: <b>{s['total_messages']}</b>",
        f"Активных пользователей: <b>{s['active_users']}</b>",
        f"Средняя задержка ответа: <b>{latency}</b>",
        f"Блокировок модерацией: <b>{s['moderation_block_rate']:.1%}</b>",
        f"👍 среди оценок: <b>{ratio}</b> (всего {s['feedback_total']})",
    ]
    if s.get("top_questions"):
        lines.append("\n<b>Частые вопросы:</b>")
        lines += [f"{i}. {e(q['question'])} — {q['count']}" for i, q in enumerate(s["top_questions"], 1)]
    spend = s.get("spend_today") or []
    lines.append(f"\n<b>Расходы сегодня</b> (лимит ${s.get('daily_limit_usd', 0):g} и "
                 f"{_n(s.get('daily_limit_tokens', 0))} токенов на человека, сброс в 00:00 МСК):")
    if not spend:
        lines.append("пока ничего")
    for r in spend[:10]:
        mark = " (админ, без лимита)" if r.get("exempt") else ""
        lines.append(f"{e(r['owner'])}{mark}: ${r['usd']:.4f}, {_n(r['tokens'])} ток., {r['messages']} сообщ.")
    total = sum(r["usd"] for r in spend)
    if spend:
        lines.append(f"Всего за сегодня: <b>${total:.4f}</b>")
    return "\n".join(lines)


def format_users(users: list[dict]) -> str:
    rows = [f"{'Пользователь':<14} {'чатов':>5}  последняя активность"]
    for u in users[:10]:
        seen = (u.get("last_seen_at") or "—")[:16].replace("T", " ")
        rows.append(f"{u['owner_external_id'][:14]:<14} {u['chats']:>5}  {seen}")
    return "<pre>" + html.escape("\n".join(rows)) + "</pre>"


@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def cmd_stats(message: Message, backend: BackendClient, settings: BotSettings) -> None:
    try:
        stats = await backend.admin_get("stats", _token(settings))
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer(format_stats(stats), parse_mode="HTML")


@router.message(Command("users"))
@router.message(F.text == BTN_USERS)
async def cmd_users(message: Message, backend: BackendClient, settings: BotSettings) -> None:
    try:
        users = await backend.admin_get("users", _token(settings), limit=10)
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer(format_users(users) if users else "Пользователей пока нет.",
                         parse_mode="HTML" if users else None)


async def _preview(message: Message, state: FSMContext, text: str) -> None:
    if len(text) > MAX_BROADCAST:
        await message.answer(f"Слишком длинно: {len(text)} символов, максимум {MAX_BROADCAST}. Пришлите текст короче.",
                             parse_mode=None, reply_markup=cancel_kb("bc:cancel"))
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(BroadcastFlow.confirming)
    await message.answer(f"Так увидят все пользователи:\n\n{text}", parse_mode=None, reply_markup=broadcast_confirm_kb())


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, state: FSMContext) -> None:
    if command.args:
        await _preview(message, state, command.args)
    else:
        await btn_broadcast(message, state)


@router.message(F.text == BTN_BROADCAST)
async def btn_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastFlow.waiting_for_text)
    await message.answer("Напишите текст рассылки одним сообщением — перед отправкой покажу, как он выглядит.",
                         parse_mode=None, reply_markup=cancel_kb("bc:cancel"))


@router.message(BroadcastFlow.waiting_for_text, F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTONS))
async def on_broadcast_text(message: Message, state: FSMContext) -> None:
    await _preview(message, state, message.text)


@router.callback_query(BroadcastFlow.confirming, F.data == "bc:send")
async def on_broadcast_send(callback: CallbackQuery, state: FSMContext, backend: BackendClient,
                            settings: BotSettings) -> None:
    text = (await state.get_data()).get("broadcast_text")
    await state.clear()
    await callback.answer()
    if not text:
        await callback.message.edit_text("Текст рассылки потерян — начните заново кнопкой 📣 Рассылка.")
        return
    try:
        result = await backend.admin_broadcast(text, _token(settings))
    except httpx.HTTPError as e:
        await callback.message.edit_text(user_message_for(e))
        return
    await callback.message.edit_text(f"Рассылка #{result.get('id')} поставлена в очередь:\n\n{text}")


@router.callback_query(F.data == "bc:cancel")
async def on_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.edit_text("Рассылка отменена — никто ничего не получил.")
