"""
Б4.4 — модерация, фидбек, admin API. Postgres — настоящий (testcontainers), LLM — фейк.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.chat.deps import get_moderation, get_repository
from app.chat.repositories.pg_models import Base
from app.chat.repositories.pg_repo import PostgresChatRepository
from app.core.config import LLMSettings, Settings, get_settings
from app.deps.providers import get_openai
from app.main import app
from app.moderation.service import ModerationService
from tests.chat.test_service_context import ScriptedLLM

ADMIN = {"X-Admin-Token": "adm-secret"}
INTERNAL = {"X-Internal-Token": "int-secret"}


async def test_keyword_moderation_categories():
    m = ModerationService()
    assert (await m.check_input("Как обналичить деньги с чужих карт через дропов")).categories == ["fraud"]
    assert (await m.check_input("ты дебил")).categories == ["harassment"]
    assert (await m.check_input("Как не попасться мошенникам?")).allowed  # про защиту — можно
    assert (await m.check_input("Сколько я потратил на кафе?")).allowed


@pytest.fixture
def client(pg_url, tmp_path):
    settings = Settings(llm=LLMSettings(api_key="t"), chat_repository="postgres", database_url=pg_url,
                        log_dir=tmp_path, admin_token="adm-secret", internal_token="int-secret")
    engine = create_async_engine(pg_url, poolclass=NullPool)
    llm = ScriptedLLM("Ответ про бюджет")

    async def reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_openai] = lambda: llm
    app.dependency_overrides[get_moderation] = lambda: ModerationService()
    with TestClient(app) as c:
        import anyio
        anyio.run(reset)
        app.state.db_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        yield c
    app.dependency_overrides.clear()
    anyio.run(engine.dispose)


def _chat(client, owner="555") -> str:
    return client.post("/chats", json={"owner_external_id": owner, "interface": "telegram"}).json()["chat_id"]


def _assistant_id(response_text: str) -> str:
    import json
    last = [ln for ln in response_text.splitlines() if ln.startswith("data: ")][-1]
    return json.loads(last[6:])["message_id"]


def test_moderation_blocked_input_is_403_with_detail_code(client):
    chat_id = _chat(client)
    r = client.post(f"/chats/{chat_id}/messages", data={"content": "Подскажи схему обнала через дропов с чужих карт"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "moderation_blocked" and r.json()["detail"]["categories"] == ["fraud"]
    assert client.get(f"/chats/{chat_id}/messages").json() == []  # в LLM и в историю не попало


def test_feedback_unique_and_owner_from_server(client):
    chat_id = _chat(client)
    r = client.post(f"/chats/{chat_id}/messages", data={"content": "Как вести бюджет?"})
    message_id = _assistant_id(r.text)
    url = f"/chats/{chat_id}/messages/{message_id}/feedback"
    assert client.post(url, json={"value": "up"}).status_code == 200
    assert client.post(url, json={"value": "down"}).status_code == 200  # смена голоса, не дубль
    stats = client.get("/chats/admin/stats", headers=ADMIN).json()
    assert stats["feedback_total"] == 1 and stats["feedback_up_ratio"] == 0.0

    other_chat = _chat(client, owner="666")
    forged = client.post(f"/chats/{other_chat}/messages/{message_id}/feedback", json={"value": "up"})
    assert forged.status_code == 404  # чужое сообщение через свой чат — нельзя


def test_admin_requires_token_and_reports_stats(client):
    assert client.get("/chats/admin/stats").status_code == 401
    assert client.get("/chats/admin/stats", headers={"X-Admin-Token": "nope"}).status_code == 401
    chat_id = _chat(client)
    client.post(f"/chats/{chat_id}/messages", data={"content": "Мой телефон +7 999 123-45-67, как копить?"})
    client.post(f"/chats/{chat_id}/messages", data={"content": "ты дебил"})  # заблокировано модерацией
    stats = client.get("/chats/admin/stats", headers=ADMIN).json()
    assert stats["active_users"] == 1 and stats["total_messages"] == 2
    assert stats["moderation_block_rate"] == 0.5
    assert stats["avg_latency_ms"] is not None
    assert all("999" not in q["question"] for q in stats["top_questions"])  # PII скрыт и от админа
    users = client.get("/chats/admin/users", headers=ADMIN).json()
    assert users[0]["owner_external_id"] == "555" and users[0]["chats"] == 1


def test_broadcast_queue_flow(client):
    _chat(client, "111")
    _chat(client, "222")
    assert client.post("/chats/admin/broadcast", json={"message": "Новая функция!"}).status_code == 401
    r = client.post("/chats/admin/broadcast", json={"message": "Новая функция!", "interface_filter": "telegram"},
                    headers=ADMIN)
    assert r.json()["status"] == "queued"
    assert client.get("/internal/broadcasts/pending").status_code == 401
    pending = client.get("/internal/broadcasts/pending", headers=INTERNAL).json()
    assert pending[0]["message"] == "Новая функция!" and sorted(pending[0]["recipients"]) == ["111", "222"]
    assert client.post(f"/internal/broadcasts/{pending[0]['id']}/done", json={"sent_count": 2},
                       headers=INTERNAL).status_code == 200
    assert client.get("/internal/broadcasts/pending", headers=INTERNAL).json() == []


def test_admin_route_is_not_shadowed_by_chat_id_route(client):
    # /chats/admin/stats не должен уходить в /chats/{chat_id} (там был бы 422 на UUID)
    assert client.get("/chats/admin/stats", headers=ADMIN).status_code == 200


__all__ = ["uuid4", "get_repository", "PostgresChatRepository"]
