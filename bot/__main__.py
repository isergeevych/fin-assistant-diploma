"""
Точка входа бота: `python -m bot` (после того как поднят backend).

Bot + Dispatcher + MemoryStorage для FSM. BackendClient и настройки кладутся в
dp["backend"] / dp["settings"] — aiogram передаёт их в хендлеры как параметры.
Один httpx.AsyncClient на всё приложение; закрывается в finally.
"""

import asyncio

import httpx
import structlog
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.observability.logging import setup_logging
from bot.config import get_bot_settings
from bot.handlers import build_router
from bot.services.backend_client import BackendClient

log = structlog.get_logger("bot")


async def main() -> None:
    setup_logging("INFO")
    settings = get_bot_settings()
    http = httpx.AsyncClient(
        base_url=settings.backend_url,
        timeout=httpx.Timeout(connect=3.0, read=settings.backend_timeout_sec, write=10.0, pool=5.0),
    )
    bot = Bot(token=settings.bot_token.get_secret_value())
    dp = Dispatcher(storage=MemoryStorage())
    dp["backend"] = BackendClient(http)
    dp["settings"] = settings
    dp.include_router(build_router())
    log.info("bot.starting", backend=settings.backend_url)
    try:
        await dp.start_polling(bot)
    finally:
        await http.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
