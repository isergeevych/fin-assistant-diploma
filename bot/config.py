"""Настройки бота (pydantic-settings). Токен — SecretStr, читается из .env (в git не попадает)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import PROJECT_ROOT


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    backend_url: str = "http://127.0.0.1:8010"
    bot_admin_ids: list[int] = []
    backend_timeout_sec: float = 30.0
    # Не чаще одного edit_text на сообщение в секунду: иначе Telegram отвечает 429 Too Many Requests
    stream_edit_interval_sec: float = 1.0
    stream_mode: Literal["draft", "edit"] = "draft"  # Б4.3: нативный sendMessageDraft или edit_text

    # Б4.3: внутренний API бота (/notify) для backend
    internal_token: SecretStr | None = None
    bot_api_port: int = 9000

    @field_validator("bot_admin_ids", mode="before")
    @classmethod
    def parse_ids(cls, v):
        # .env удобнее как "123,456", pydantic-settings по умолчанию ждёт JSON-список
        if isinstance(v, str):
            return [int(x) for x in v.replace(" ", "").split(",") if x]
        return v


@lru_cache
def get_bot_settings() -> BotSettings:
    return BotSettings()
