"""
Б4.3 — обратный канал backend → bot: проактивные уведомления
(«бюджет на кафе превышен», «отчёт готов»). Бот слушает POST /notify во внутренней сети.
"""

import httpx

from app.core.config import Settings


async def notify_user(settings: Settings, chat_id_tg: int, text: str) -> None:
    if settings.internal_token is None:
        raise RuntimeError("INTERNAL_TOKEN не задан — уведомления в бота выключены")
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(
            f"{settings.bot_url}/notify",
            json={"chat_id": chat_id_tg, "text": text},
            headers={"X-Internal-Token": settings.internal_token.get_secret_value()},
        )
        r.raise_for_status()
