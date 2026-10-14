"""Б4.4 — бот: IsAdmin, разбор кнопок фидбека, форматирование /stats, замена ответа по модерации."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from bot.handlers.admin import IsAdmin, format_stats, format_users
from bot.handlers.feedback import parse_feedback
from bot.services.backend_client import BackendClient
from bot.services.render import stream_to_message


async def test_is_admin_filter_checks_id_list():
    settings = SimpleNamespace(bot_admin_ids=[42])
    assert await IsAdmin()(SimpleNamespace(from_user=SimpleNamespace(id=42)), settings) is True
    assert await IsAdmin()(SimpleNamespace(from_user=SimpleNamespace(id=7)), settings) is False


def test_parse_feedback_rejects_forged_data():
    mid = uuid4()
    assert parse_feedback(f"fb:up:{mid}") == ("up", mid)
    assert parse_feedback(f"fb:down:{mid}") == ("down", mid)
    for forged in [f"fb:delete:{mid}", "fb:up:not-a-uuid", f"fb:up:{mid}:extra", "topic:taxes"]:
        assert parse_feedback(forged) is None


def test_format_stats_escapes_html():
    stats = {"total_messages": 3, "active_users": 1, "avg_latency_ms": 812.4, "moderation_block_rate": 0.25,
             "feedback_up_ratio": 0.5, "feedback_total": 2,
             "top_questions": [{"question": "<b>взлом</b> & <script>", "count": 2}]}
    text = format_stats(stats)
    assert "&lt;b&gt;взлом&lt;/b&gt; &amp; &lt;script&gt;" in text and "<script>" not in text
    assert "812 мс" in text and "25.0%" in text
    assert "<pre>" in format_users([{"owner_external_id": "<x>", "chats": 1, "last_seen_at": None}])


async def test_replace_event_replaces_shown_text_and_hides_feedback():
    body = (
        'data: {"type": "token", "delta": "Плохой ответ"}\n\n'
        'data: {"type": "replace", "text": "Не могу показать ответ — он мог нарушить правила."}\n\n'
        'data: {"type": "done", "message_id": null}\n\n'
    ).encode()
    backend = BackendClient(httpx.AsyncClient(base_url="http://b", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=body))))
    reply = MagicMock()
    reply.edit_text = AsyncMock()
    message = MagicMock()
    message.answer = AsyncMock(return_value=reply)

    ok = await stream_to_message(message, backend.send_message(uuid4(), "q"), interval_sec=0,
                                 final_markup=lambda: "KEYBOARD")
    assert ok is False
    final_call = reply.edit_text.call_args
    assert final_call.kwargs["text"] == "Не могу показать ответ — он мог нарушить правила."
    assert final_call.kwargs["reply_markup"] is None  # за отмодерированный ответ не голосуют


async def test_done_event_gives_message_id_for_feedback():
    mid = str(uuid4())
    body = f'data: {{"type": "token", "delta": "ок"}}\n\ndata: {{"type": "done", "message_id": "{mid}"}}\n\n'.encode()
    backend = BackendClient(httpx.AsyncClient(base_url="http://b", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=body))))
    meta: dict = {}
    assert [c async for c in backend.send_message(uuid4(), "q", meta=meta)] == ["ок"]
    assert meta["message_id"] == mid


async def test_admin_calls_send_admin_token_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.headers.get("x-admin-token")
        return httpx.Response(200, json={"id": 1} if request.method == "POST" else [])

    backend = BackendClient(httpx.AsyncClient(base_url="http://b", transport=httpx.MockTransport(handler)))
    await backend.admin_get("users", "tok", limit=10)
    await backend.admin_broadcast("hello", "tok")
    assert seen == {"/chats/admin/users": "tok", "/chats/admin/broadcast": "tok"}


__all__ = ["json", "pytest"]
