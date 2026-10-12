"""Б3.6 — маскирование PII. Тест обязан падать, если маскирование снять."""

import json
import logging

import pytest
import structlog

from app.observability.logging import setup_logging
from app.observability.pii import preview, prompt_hash, redact_pii

RAW = "Мой email ivan@mail.ru, тел +7 (999) 123-45-67, карта 4111 1111 1111 1111"


def test_course_example_has_no_raw_pii():
    masked = preview(RAW)
    for fragment in ("ivan@mail.ru", "999", "123-45-67", "4111"):
        assert fragment not in masked
    for placeholder in ("[EMAIL]", "[PHONE_RU]", "[CARD]"):
        assert placeholder in masked


def test_checklist_example():
    masked = redact_pii("email@example.com, +7 999 123 45 67")
    assert "email@example.com" not in masked and "999" not in masked
    assert "[EMAIL]" in masked and "[PHONE_RU]" in masked


@pytest.mark.parametrize(
    ("text", "placeholder", "raw"),
    [
        ("звоните 8-999-123-45-67", "[PHONE_RU]", "999-123"),
        ("ИНН 7707083893", "[INN]", "7707083893"),
        ("ИНН физлица 500100732259", "[INN]", "500100732259"),
        ("паспорт 45 08 123456", "[PASSPORT]", "123456"),
        ("счёт 40817810099910004312", "[ACCOUNT]", "40817810099910004312"),
        ("карта 2200-1234-5678-9010", "[CARD]", "5678"),
    ],
)
def test_patterns(text, placeholder, raw):
    masked = redact_pii(text)
    assert placeholder in masked and raw not in masked


def test_ordinary_numbers_are_not_masked():
    text = "Потратил 12 500 ₽ на 3 покупки в 2026 году"
    assert redact_pii(text) == text


def test_prompt_hash_is_stable_and_short():
    assert prompt_hash("a") == prompt_hash("a") != prompt_hash("b")
    assert prompt_hash("a").startswith("sha256:") and len(prompt_hash("a")) == 7 + 16


def test_structlog_processor_masks_any_string_field(tmp_path, capsys):
    setup_logging("INFO", log_file=tmp_path / "log.jsonl")
    structlog.get_logger("test").info("user_said", text=RAW, request_id="abc123")
    logging.getLogger("stdlib").info("legacy", extra={"fields": {"note": "почта ivan@mail.ru"}})
    lines = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all("ivan@mail.ru" not in json.dumps(line, ensure_ascii=False) for line in lines)
    assert lines[0]["request_id"] == "abc123" and "[CARD]" in lines[0]["text"]
