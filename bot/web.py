"""
Б4.3 — внутренний HTTP API бота: backend дёргает POST /notify для проактивных уведомлений.

Защита: X-Internal-Token (общий секрет app ↔ bot из .env), сравнение compare_digest.
Порт 9000 слушается внутри контейнера, но НЕ пробрасывается на хост (см. docker-compose.yml) —
снаружи достучаться до /notify нельзя.
"""

import secrets

from aiogram import Bot
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


class NotifyRequest(BaseModel):
    chat_id: int
    text: str = Field(min_length=1, max_length=4096)


def build_api(bot: Bot, internal_token: str) -> FastAPI:
    api = FastAPI(title="bot-internal", docs_url=None, redoc_url=None, openapi_url=None)

    @api.post("/notify")
    async def notify(req: NotifyRequest, x_internal_token: str = Header(default="")) -> dict:
        if not internal_token or not secrets.compare_digest(x_internal_token.encode(), internal_token.encode()):
            raise HTTPException(status_code=401)
        await bot.send_message(chat_id=req.chat_id, text=req.text, parse_mode=None)
        return {"ok": True}

    @api.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return api
