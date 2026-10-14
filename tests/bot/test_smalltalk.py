"""П2/П6: короткие реплики отвечаются без LLM, вопросы — уходят в backend; П4: понятный текст при input_rejected."""

import httpx
import pytest

from bot.handlers.errors import INPUT_REJECTED_TEXT, user_message_for
from bot.services.smalltalk import canned_reply


@pytest.mark.parametrize("text", ["привет", "Привет!", "  Здравствуйте  ", "Добрый день!", "старт", "Начнём", "СТАРТ"])
def test_greetings_get_greeting(text):
    assert canned_reply(text).startswith("Здравствуйте! Я Финпомощник")


@pytest.mark.parametrize("text", ["спасибо", "Спасибо большое!", "ок", "Понятно."])
def test_thanks_and_ok(text):
    reply = canned_reply(text)
    assert reply and "По базе" not in reply


@pytest.mark.parametrize("text", ["???", "...", "🤔🤔", "!!!"])
def test_no_words_is_unclear(text):
    assert canned_reply(text).startswith("Не понял вопрос")


@pytest.mark.parametrize("text", ["привет, сколько вернут за лечение?", "а сколько?", "ок, а вклады?", "старт продаж",
                                  "Сколько можно вернуть за лечение зубов?", "", None])
def test_real_messages_go_to_backend(text):
    assert canned_reply(text) is None


@pytest.mark.parametrize("text", ["👍", "👍👍", "🙏", "❤️"])
def test_approval_emoji_is_ok(text):
    assert canned_reply(text).startswith("Хорошо")


def _http_error(status: int, body) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://app/chats/1/messages")
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


def test_input_rejected_explains_instead_of_generic_text():
    exc = _http_error(400, {"error": {"code": "input_rejected", "message": "rejected"}})
    assert user_message_for(exc) == INPUT_REJECTED_TEXT


def test_other_400_keeps_generic_text():
    assert user_message_for(_http_error(400, {"error": {"code": "bad"}})).startswith("Не могу обработать")
    assert user_message_for(_http_error(400, ["not", "a", "dict"])).startswith("Не могу обработать")
