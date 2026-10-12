"""Б4.1 — контракт ChatRepository: одни и те же тесты для JSON и Postgres."""

import asyncio
from uuid import uuid4

from app.chat.domain import ChatMessage


async def add(repo, chat_id, role, content):
    msg = await repo.append_message(chat_id, ChatMessage(chat_id=chat_id, role=role, content=content))
    await asyncio.sleep(0.002)  # разные created_at — порядок однозначен
    return msg


async def test_create_and_get_chat(repo):
    chat = await repo.create_chat("tg:1", "telegram", system_prompt="Ты помощник")
    loaded = await repo.get_chat(chat.id)
    assert loaded is not None
    assert loaded.id == chat.id and loaded.owner_external_id == "tg:1" and loaded.system_prompt == "Ты помощник"


async def test_messages_are_chronological(repo):
    chat = await repo.create_chat("tg:1", "telegram")
    for i, role in enumerate(["user", "assistant", "user", "assistant"]):
        await add(repo, chat.id, role, f"m{i}")
    messages = await repo.list_messages(chat.id)
    assert [m.content for m in messages] == ["m0", "m1", "m2", "m3"]
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]


async def test_limit_returns_last_n_not_first(repo):
    chat = await repo.create_chat("tg:1", "cli")
    for i in range(6):
        await add(repo, chat.id, "user", f"m{i}")
    assert [m.content for m in await repo.list_messages(chat.id, limit=3)] == ["m3", "m4", "m5"]


async def test_soft_delete_hides_old_but_new_visible(repo):
    chat = await repo.create_chat("tg:1", "cli")
    await add(repo, chat.id, "user", "старое")
    await repo.soft_delete_messages(chat.id)
    assert await repo.list_messages(chat.id) == []
    await add(repo, chat.id, "user", "новое")
    assert [m.content for m in await repo.list_messages(chat.id)] == ["новое"]


async def test_unknown_chat_returns_none_and_empty(repo):
    unknown = uuid4()
    assert await repo.get_chat(unknown) is None
    assert await repo.list_messages(unknown) == []


async def test_find_latest_chat_for_owner_and_interface(repo):
    first = await repo.create_chat("tg:1", "telegram")
    await asyncio.sleep(0.01)
    second = await repo.create_chat("tg:1", "telegram")
    await repo.create_chat("tg:1", "web")
    await repo.create_chat("tg:2", "telegram")
    latest = await repo.find_latest_chat("tg:1", "telegram")
    assert latest is not None and latest.id == second.id != first.id
    assert await repo.find_latest_chat("tg:404", "telegram") is None
