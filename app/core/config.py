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
    request_timeout: float = 30.0  # таймаут одного HTTP-запроса в SDK
    call_timeout: float = 15.0  # бизнес-дедлайн на попытку у одного провайдера (asyncio.timeout)
    max_retries: int = 3  # встроенные ретраи SDK на 429/5xx/таймауты (экспонента + Retry-After)
    max_concurrency: int = 5  # Bulkhead: одновременных вызовов LLM на процесс

    # Fallback-цепочка (ADR-002): другой вендор через тот же OpenRouter, затем локальная модель.
    fallback_model: str | None = "anthropic/claude-haiku-4.5"
    local_base_url: str | None = "http://localhost:11434/v1"
    local_model: str = "gemma3:4b"

    # Circuit breaker — по одному на провайдера
    cb_fail_max: int = 5
    cb_reset_timeout: float = 60.0


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

    # --- HTTP-сервис (Б3.4) ---
    # Не localhost: на Windows он сначала резолвится в ::1, а Redis слушает только IPv4.
    redis_url: str = "redis://127.0.0.1:6379/0"
    cache_ttl_seconds: int = 3600
    # Явный список origin'ов. "*" вместе с credentials запрещён (CORS-спецификация + безопасность).
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]
    log_level: str = "INFO"

    # --- Безопасность (Б3.8) ---
    # false — «голый» сервис для baseline-прогона garak; в проде всегда true.
    security_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
