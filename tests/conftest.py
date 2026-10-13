"""Общие настройки тестов: внешние тяжёлые компоненты при старте приложения не поднимаются."""

import os

# Тесты в сеть не ходят, но настройки с обязательным ключом читаются при импорте (main, граф агента):
# на чистой машине без .env без этой строки не собрался бы ни один тест.
os.environ.setdefault("LLM__API_KEY", "test-key-not-used")
# RAG-индекс строится в lifespan (Qdrant + эмбеддинги) — unit-тестам он не нужен.
os.environ.setdefault("RAG_ENABLED", "false")
os.environ.setdefault("AGENT_CHECKPOINTER", "memory")  # Б6.4: TestClient не создаёт var/agent_checkpoints.sqlite
# лимиты на пользователя ходят в Redis: в общих тестах выключены, свои тесты — tests/unit/test_usage_limits.py
for _limit in ("USAGE_DAILY_USD", "USAGE_DAILY_TOKENS", "USAGE_RATE_PER_MINUTE"):
    os.environ.setdefault(_limit, "0")
