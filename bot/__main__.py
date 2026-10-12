"""
Точка входа бота: `python -m bot` (после того как поднят backend).

В одном event loop работают polling Telegram и внутренний HTTP API (/notify, Б4.3).
BackendClient и настройки кладутся в dp["backend"] / dp["settings"] — aiogram передаёт их
в хендлеры как параметры. Один httpx.AsyncClient на всё приложение; закрывается в finally.
"""

import asyncio
import os

import httpx
import structlog
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.observability.logging import setup_logging
from bot.config import get_bot_settings
from bot.handlers import build_router
from bot.services.backend_client import BackendClient
from bot.web import build_api

log = structlog.get_logger("bot")


async def main() -> None:
    setup_logging("INFO")
    if not os.environ.get("BOT_TOKEN"):
        # Без токена бот не нужен — выходим чисто (код 0), чтобы `docker compose up` работал и до
        # получения токена, а restart: on-failure не устраивал цикл перезапусков.
        log.warning("bot.disabled", reason="BOT_TOKEN не задан")
        return
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

    token = settings.internal_token.get_secret_value() if settings.internal_token else ""
    server = uvicorn.Server(uvicorn.Config(build_api(bot, token), host="0.0.0.0", port=settings.bot_api_port,
                                           log_level="warning"))
    log.info("bot.starting", backend=settings.backend_url, api_port=settings.bot_api_port)
    try:
        await asyncio.gather(dp.start_polling(bot), server.serve())
    finally:
        await http.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
