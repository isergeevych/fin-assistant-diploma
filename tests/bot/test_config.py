"""BOT_ADMIN_IDS в .env пишут как «123» или «123,456» — бот не должен падать на старте."""

import pytest

from bot.config import BotSettings


@pytest.mark.parametrize(("raw", "expected"), [("123", [123]), ("123,456", [123, 456]), (" 1 , 2 ", [1, 2]), ("", [])])
def test_admin_ids_formats(monkeypatch, raw, expected):
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("BOT_ADMIN_IDS", raw)
    assert BotSettings(_env_file=None).bot_admin_ids == expected
