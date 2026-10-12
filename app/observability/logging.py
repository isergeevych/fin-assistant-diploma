"""
structlog: JSON-строка на событие, request_id и прочий контекст — через contextvars.

Одна конфигурация на оба мира:
- structlog.get_logger(...) в нашем коде;
- stdlib logging (uvicorn, библиотеки) идёт через тот же ProcessorFormatter,
  поэтому всё выходит одинаковым JSON и с тем же request_id.

Последний рубеж защиты: процессор redact_event прогоняет все строковые поля
через redact_pii — даже если кто-то по ошибке положит сырой текст в лог,
email/телефон/карта туда не попадут.
"""

import logging
import sys
from pathlib import Path

import structlog

from app.observability.pii import redact_pii

# Поля-идентификаторы: в них нет PII, а маскер мог бы испортить, например, длинные числа.
SAFE_KEYS = {"event", "level", "timestamp", "logger", "request_id", "prompt_hash", "path", "method", "model"}


def redact_event(_logger, _method, event_dict: dict) -> dict:
    for key, value in event_dict.items():
        if key not in SAFE_KEYS and isinstance(value, str):
            event_dict[key] = redact_pii(value)
    return event_dict


def _stdlib_fields(_logger, _method, event_dict: dict) -> dict:
    """Поддержка старого стиля logging.info(event, extra={"fields": {...}}) из Б3.1."""
    record = event_dict.get("_record")
    if record is not None and isinstance(getattr(record, "fields", None), dict):
        event_dict.update(record.fields)
    return event_dict


SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def setup_logging(level: str | int = "INFO", log_file: Path | None = None) -> None:
    level = logging.getLevelName(level) if isinstance(level, str) else level
    structlog.configure(
        processors=[*SHARED_PROCESSORS, redact_event, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*SHARED_PROCESSORS, _stdlib_fields, redact_event],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    root = logging.getLogger()
    root.handlers.clear()
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
