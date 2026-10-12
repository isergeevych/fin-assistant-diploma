"""Реэкспорт: часть заданий (3.1, 5.2, 5.5) ссылается на app/config.py,
часть (3.4) — на app/core/config.py. Источник один — app/core/config.py."""

from app.core.config import LLMSettings, Settings, get_settings

__all__ = ["LLMSettings", "Settings", "get_settings"]
