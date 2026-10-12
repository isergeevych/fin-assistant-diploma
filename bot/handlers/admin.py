"""
Б4.4 — admin-команды: /stats, /users, /broadcast <текст>.

Доступ — фильтр IsAdmin на уровне РОУТЕРА (admin_router.message.filter(IsAdmin())), а не if
внутри каждого хендлера: новый хендлер в этом роутере не сможет «забыть» проверку.
Для не-админа команды молча не срабатывают (не раскрываем, что они существуют).
Данные backend форматируются в HTML, все значения экранируются (html.escape).
"""

import html

import httpx
from aiogram import Router
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

from bot.config import BotSettings
from bot.handlers.errors import user_message_for
from bot.services.backend_client import BackendClient

router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message, settings: BotSettings) -> bool:
        return message.from_user is not None and message.from_user.id in settings.bot_admin_ids


router.message.filter(IsAdmin())


def _token(settings: BotSettings) -> str:
    return settings.admin_token.get_secret_value() if settings.admin_token else ""


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
    return "\n".join(lines)


def format_users(users: list[dict]) -> str:
    rows = [f"{'Пользователь':<14} {'чатов':>5}  последняя активность"]
    for u in users[:10]:
        seen = (u.get("last_seen_at") or "—")[:16].replace("T", " ")
        rows.append(f"{u['owner_external_id'][:14]:<14} {u['chats']:>5}  {seen}")
    return "<pre>" + html.escape("\n".join(rows)) + "</pre>"


@router.message(Command("stats"))
async def cmd_stats(message: Message, backend: BackendClient, settings: BotSettings) -> None:
    try:
        stats = await backend.admin_get("stats", _token(settings))
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer(format_stats(stats), parse_mode="HTML")


@router.message(Command("users"))
async def cmd_users(message: Message, backend: BackendClient, settings: BotSettings) -> None:
    try:
        users = await backend.admin_get("users", _token(settings), limit=10)
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer(format_users(users) if users else "Пользователей пока нет.",
                         parse_mode="HTML" if users else None)


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, backend: BackendClient,
                        settings: BotSettings) -> None:
    if not command.args:
        await message.answer("Использование: /broadcast <текст рассылки>", parse_mode=None)
        return
    try:
        result = await backend.admin_broadcast(command.args, _token(settings))
    except httpx.HTTPError as e:
        await message.answer(user_message_for(e), parse_mode=None)
        return
    await message.answer(f"Рассылка #{result.get('id')} поставлена в очередь.", parse_mode=None)
