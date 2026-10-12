"""
Структурные JSON-логи (по одной JSON-строке на событие).

Пока — stdlib logging с JSON-форматтером; в Б3.6 его заменит structlog с
теми же именами событий и полей, поэтому формат выбран совместимым.
Поля события передаются через extra={"fields": {...}}.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_json_logging(log_dir: Path, filename: str = "app.jsonl", level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()
    root = logging.getLogger()
    root.setLevel(level)
    for handler in (logging.StreamHandler(), logging.FileHandler(log_dir / filename, encoding="utf-8")):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
