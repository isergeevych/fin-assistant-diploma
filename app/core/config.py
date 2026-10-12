"""
Единственный источник конфигурации проекта (pydantic-settings v2).

Значения читаются из переменных окружения и файла .env. Вложенные группы —
через двойное подчёркивание: LLM__API_KEY, LLM__DEFAULT_MODEL и т. д.
Секреты хранятся как SecretStr, поэтому не попадают в repr и логи.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator
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
    prompt_version: str = "v2"  # v2 (Б3.7, 13.10): чужие данные — явный отказ; без лишних уточнений периода
    product_name: str = "Финпомощник"

    # Финансовые данные пользователей (до модуля 4 — CSV-файл)
    transactions_csv: Path = PROJECT_ROOT / "data" / "finance" / "transactions.csv"
    # Внешний API: официальные курсы ЦБ РФ (ключ не нужен)
    cbr_daily_url: str = "https://www.cbr.ru/scripts/XML_daily.asp"
    # Резерв: cbr.ru не отвечает с зарубежных IP. Зеркало публикует тот же XML ЦБ (неофициальное — в ответе
    # tool помечается source). Пусто — без резерва.
    cbr_mirror_url: str | None = "https://www.cbr-xml-daily.ru/daily.xml"
    cbr_mirror_archive_url: str = "https://www.cbr-xml-daily.ru/archive/{y}/{m}/{d}/daily.xml"

    log_dir: Path = PROJECT_ROOT / "logs"

    # --- HTTP-сервис (Б3.4) ---
    # Не localhost: на Windows он сначала резолвится в ::1, а Redis слушает только IPv4.
    redis_url: str = "redis://127.0.0.1:6379/0"
    cache_ttl_seconds: int = 3600
    # Явный список origin'ов. "*" вместе с credentials запрещён (CORS-спецификация + безопасность).
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]
    log_level: str = "INFO"

    # --- Чат и история (Б4.1) ---
    chat_repository: Literal["json", "postgres"] = "json"
    chat_storage_dir: Path = PROJECT_ROOT / "var" / "chats"
    chat_context_strategy: Literal["sliding", "hybrid"] = "sliding"
    chat_context_window: int = 10  # сколько последних сообщений истории идёт в контекст
    # Токен-бюджет запроса: CONTEXT - RESPONSE - SAFETY. Окно модели 400k, но бюджет держим
    # маленьким сознательно — длинная история дорога и почти не нужна финансовому ассистенту.
    llm_context_tokens: int = 16_000
    llm_response_tokens: int = 1_024
    llm_safety_margin_tokens: int = 512
    database_url: str = "postgresql+asyncpg://fin:fin@127.0.0.1:5433/fin"

    # --- Мультимодальность и обратный канал в бота (Б4.3) ---
    # Whisper (голос → текст) есть только у OpenAI напрямую; без ключа голосовые отклоняются с 503.
    openai_api_key: SecretStr | None = None
    bot_url: str = "http://bot:9000"
    internal_token: SecretStr | None = None  # общий секрет app ↔ bot для /notify и системных эндпоинтов
    bot_api_port: int = 9000

    # --- Production-обвязка (Б4.4) ---
    moderation_enabled: bool = True
    admin_token: SecretStr | None = None  # X-Admin-Token для /chats/admin/*; без него admin-API выключен

    # --- Эмбеддинги (Б5.1) ---
    # OpenAI-совместимый /v1/embeddings: локально — Ollama (GPU, без ключа), в проде — OpenRouter
    # (там есть та же baai/bge-m3 — смена провайдера = смена base_url, векторы того же семейства).
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_api_key: SecretStr = SecretStr("ollama")
    embedding_model: str = "qwen3-embedding:0.6b"  # выбор по mini-benchmark — docs/embeddings.md
    embedding_dim: int = 1024
    # Асимметричная модель: к ЗАПРОСУ добавляется инструкция, документы — как есть.
    # На mini-benchmark минимальный зазор rel−irr вырос с +0.036 до +0.099.
    embedding_query_instruction: str = (
        "Instruct: Given a user question about personal finance, retrieve the passage that answers it\nQuery: "
    )
    embedding_batch_size: int = 64
    embedding_cache_dir: Path = PROJECT_ROOT / "var" / "embeddings_cache"

    # --- Векторная БД (Б5.2) ---
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "documents"
    data_dir: Path = PROJECT_ROOT / "data"

    # --- RAG (Б5.3) ---
    rag_enabled: bool = True
    chat_use_rag: bool = True  # Б5.5: /chats/{id}/messages отвечает с опорой на базу знаний
    rag_data_dir: Path = PROJECT_ROOT / "data" / "rag-block-03"  # Б5.3: 10 документов
    rag_collection: str = "knowledge_base"          # Б5.5: вся база (LlamaIndex-формат нод _node_content)
    rag_collection_bare: str = "rag_block_03_bare"  # bare-metal (плоский payload)
    docstore_dir: Path = PROJECT_ROOT / "var" / "docstore"  # хеши документов для UPSERTS (повторный ingest)
    rag_condense_enabled: bool = True   # переписывать follow-up («а для них?») в самостоятельный вопрос для поиска
    rag_confident_score: float = 0.5    # флаг confident в ответе
    rag_rerank_threshold: float = 0.1   # score-guard по re-ranker (если он работал) — калибровка в docs/rag.md
    # Итог Б5.4 (docs/chunking_experiment.md): recursive (SentenceSplitter), 512/64, retrieval top-10 → rerank top-5
    rag_chunk_strategy: Literal["fixed", "recursive", "semantic"] = "recursive"
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 64
    rag_top_k: int = 3
    rag_retrieval_top_k: int = 10
    rag_rerank_enabled: bool = True  # нужен extra `rerank` (torch); без него — только векторный поиск
    rag_rerank_top_n: int = 5
    rag_score_threshold: float = 0.35  # ниже — честный отказ без вызова LLM (калибровка — docs/rag.md)
    # LLM для генерации ответа. Пусто — берётся основной LLM (OpenRouter); для локальной работы — Ollama.
    rag_llm_base_url: str | None = None
    rag_llm_model: str | None = None
    rag_llm_api_key: SecretStr | None = None
    # Reasoning-модели (qwen3, gpt-5.x) тратят токены на «размышления»: на condense qwen3 съедала весь лимит
    # и возвращала пустой ответ. "none" — без размышлений (Ollama: 2.5 с вместо обрыва); пусто — не передавать.
    rag_llm_reasoning_effort: str | None = "none"

    # --- Оценка RAG (Б5.6) ---
    # Судья ≠ production-модель (роль другая). Пусто — берётся RAG-LLM (локально qwen3:8b).
    eval_judge_base_url: str | None = None
    eval_judge_model: str | None = None
    eval_judge_api_key: SecretStr | None = None
    # Gemini не даёт выключить reasoning ("none" → 400), поэтому у судьи своя настройка; пусто — как у RAG-LLM
    eval_judge_reasoning_effort: str | None = None
    phoenix_llamaindex_tracing: bool = False  # LlamaIndexInstrumentor в lifespan (Б5.6)

    # --- Агенты (модуль 6) ---
    # Канон курса — gpt-5.4-mini через OpenRouter (пусто = основной LLM); локально — qwen3:8b в Ollama (умеет tools).
    agent_base_url: str | None = None
    agent_model: str | None = None
    agent_api_key: SecretStr | None = None
    agent_reasoning_effort: str | None = None  # qwen3: "none" — без «размышлений» на каждом шаге

    # --- Безопасность (Б3.8) ---
    # false — «голый» сервис для baseline-прогона garak; в проде всегда true.
    security_enabled: bool = True

    @field_validator("openai_api_key", "internal_token", "admin_token", "qdrant_api_key", "rag_llm_base_url",
                     "rag_llm_model", "rag_llm_api_key", "rag_llm_reasoning_effort", "eval_judge_base_url",
                     "eval_judge_model", "eval_judge_api_key", "eval_judge_reasoning_effort", "agent_base_url",
                     "agent_model", "agent_api_key", "agent_reasoning_effort", mode="before")
    @classmethod
    def empty_is_unset(cls, v):
        # «OPENAI_API_KEY=» в .env — это «не задано», а не пустой ключ: иначе SecretStr("") проходит проверки
        # «ключ есть?» и клиент OpenAI падает с Missing credentials (поймано тестами 4.3/4.4)
        return None if isinstance(v, str) and not v.strip() else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
