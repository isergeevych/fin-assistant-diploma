"""Общие настройки тестов: внешние тяжёлые компоненты при старте приложения не поднимаются."""

import os

# RAG-индекс строится в lifespan (Qdrant + эмбеддинги) — unit-тестам он не нужен.
os.environ.setdefault("RAG_ENABLED", "false")
