"""Голосовое: в базе знаний ищется расшифровка, а не подпись-заглушка бота «[голосовое сообщение]»."""

from app.chat.media import VOICE_PREFIX
from app.chat.service import search_question

VOICE = {"type": "text", "text": VOICE_PREFIX + "Сколько можно вернуть за лечение зубов?"}


def test_voice_placeholder_caption_is_replaced_by_transcript():
    assert search_question("[голосовое сообщение]", VOICE) == "Сколько можно вернуть за лечение зубов?"


def test_real_caption_is_kept_together_with_transcript():
    assert search_question("про стоматологию", VOICE) == "про стоматологию\nСколько можно вернуть за лечение зубов?"


def test_text_and_documents_search_by_user_text():
    assert search_question("Что в этом договоре?", {"type": "text", "text": "[документ PDF]:\nДоговор…"}) == "Что в этом договоре?"
    assert search_question("Что на фото?", {"type": "image_url", "image_url": {"url": "data:…"}}) == "Что на фото?"
    assert search_question("Сколько вернут за лечение?", None) == "Сколько вернут за лечение?"
