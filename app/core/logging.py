"""Совместимость с Б3.1: setup_json_logging теперь — тонкая обёртка над structlog-конфигом из Б3.6."""

import logging
from pathlib import Path

from app.observability.logging import setup_logging


def setup_json_logging(log_dir: Path, filename: str = "app.jsonl", level: int = logging.INFO) -> None:
    setup_logging(level, log_file=log_dir / filename)
