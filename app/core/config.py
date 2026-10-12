"""
Единственный источник конфигурации проекта (pydantic-settings v2).

Значения читаются из переменных окружения и файла .env. Вложенные группы —
через двойное подчёркивание: LLM__API_KEY, LLM__DEFAULT_MODEL и т. д.
Секреты хранятся как SecretStr, поэтому не попадают в repr и логи.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LLMSettings(BaseModel):
    # OpenRouter — OpenAI-совместимый API (решение D-02 в плане).
    api_key: SecretStr
    base_url: str = "https://openrouter.ai/api/v1"
    default_model: str = "openai/gpt-5.4-mini"
    request_timeout: float = 30.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    llm: LLMSettings
    prompt_version: str = "v1"
    product_name: str = "Финпомощник"

    # Финансовые данные пользователей (до модуля 4 — CSV-файл)
    transactions_csv: Path = PROJECT_ROOT / "data" / "finance" / "transactions.csv"
    # Внешний API: официальные курсы ЦБ РФ (ключ не нужен)
    cbr_daily_url: str = "https://www.cbr.ru/scripts/XML_daily.asp"

    log_dir: Path = PROJECT_ROOT / "logs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
