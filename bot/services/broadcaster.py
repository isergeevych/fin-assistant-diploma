"""
Б4.4 — фоновая отправка рассылок: раз в broadcast_poll_sec бот забирает pending из backend
(/internal/broadcasts/pending, X-Internal-Token), шлёт получателям и отмечает отправку.

Темп ~20 сообщений в секунду — ниже лимита Telegram (~30/с на бота): иначе 429 и потерянные сообщения.
Пользователь, заблокировавший бота, пропускается — рассылка не падает целиком.
"""

import asyncio

import httpx
import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.config import BotSettings
from bot.services.backend_client import BackendClient

log = structlog.get_logger("bot.broadcast")
SEND_INTERVAL_SEC = 0.05


async def broadcast_loop(bot: Bot, backend: BackendClient, settings: BotSettings) -> None:
    token = settings.internal_token.get_secret_value() if settings.internal_token else ""
    if not token:
        log.warning("broadcast.disabled", reason="INTERNAL_TOKEN не задан")
        return
    while True:
        try:
            for item in await backend.pending_broadcasts(token):
                sent = 0
                for recipient in item["recipients"]:
                    try:
                        await bot.send_message(chat_id=int(recipient), text=item["message"], parse_mode=None)
                        sent += 1
                    except (TelegramAPIError, ValueError) as e:
                        log.warning("broadcast.skip", recipient_hash=hash(recipient) % 10_000, error=type(e).__name__)
                    await asyncio.sleep(SEND_INTERVAL_SEC)
                await backend.mark_broadcast_done(item["id"], sent, token)
                log.info("broadcast.sent", id=item["id"], sent=sent, total=len(item["recipients"]))
        except httpx.HTTPError as e:
            log.warning("broadcast.backend_error", error=type(e).__name__)
        await asyncio.sleep(settings.broadcast_poll_sec)
