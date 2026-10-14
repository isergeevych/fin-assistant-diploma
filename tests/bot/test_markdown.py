"""П1: Markdown модели → безопасный HTML Telegram (без звёздочек, без внедрённой разметки и кликабельных ссылок)."""

import pytest
from aiogram.exceptions import TelegramBadRequest

from bot.services.markdown import to_plain, to_telegram_html
from bot.services.render import _send


@pytest.mark.parametrize(("src", "expected"), [
    ("Общие расходы: **58 000 ₽**", "Общие расходы: <b>58 000 ₽</b>"),
    ("### Социальные вычеты\nК ним: **лечение**", "<b>Социальные вычеты</b>\nК ним: <b>лечение</b>"),
    ("- раз\n- два", "• раз\n• два"),
    ("стрим оборвался на **жир", "стрим оборвался на жир"),
    ("5*3=15 и 12 000", "5*3=15 и 12 000"),
    ("код `a**2`", "код <code>a**2</code>"),
])
def test_markdown_to_telegram_html(src, expected):
    assert to_telegram_html(src) == expected


def test_model_cannot_inject_html_or_clickable_links():
    out = to_telegram_html("[Получить вычет](https://evil.example/pay) <a href='x'>жми</a>")
    assert "<a" not in out and "&lt;a href" in out  # тег только экранированным текстом
    assert "Получить вычет (https://evil.example/pay)" in out and "&lt;a" in out


def test_plain_fallback_has_no_markers():
    assert to_plain("Итого: **74 000 ₽**") == "Итого: 74 000 ₽"


async def test_send_falls_back_to_plain_when_telegram_rejects_html():
    calls = []

    async def method(**kwargs):
        calls.append(kwargs)
        if kwargs["parse_mode"] == "HTML":
            raise TelegramBadRequest(method=None, message="Bad Request: can't parse entities")

    await _send(method, "Итого: **74 000 ₽**")
    assert calls[-1] == {"text": "Итого: 74 000 ₽", "parse_mode": None}
