"""Лимиты на пользователя на живом маршруте: траты из usage модели копятся, при $1 — 429 с понятным текстом."""

from app.chat.deps import get_chat_service
from app.main import app
from app.services.usage_limits import UsageLimiter, record_usage
from tests.app.chat.test_media import client  # noqa: F401 — фикстура TestClient с ChatService на JSON-хранилище
from tests.chat.test_service_context import ScriptedLLM
from tests.unit.test_usage_limits import FakeRedis


class PricedLLM(ScriptedLLM):
    """Как настоящий AsyncLLMClient: после стрима сообщает usage со стоимостью."""

    async def stream_events(self, messages, **params):
        async for event in super().stream_events(messages, **params):
            yield event
        record_usage({"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.6}, "fake")


def test_daily_dollar_limit_end_to_end(client):  # noqa: F811
    llm = PricedLLM("ответ")
    service = app.dependency_overrides[get_chat_service]()  # сервис из фикстуры, но с «платной» моделью
    service.llm = llm
    app.dependency_overrides[get_chat_service] = lambda: service
    app.state.usage_limiter = UsageLimiter(FakeRedis(), daily_usd=1.0, daily_tokens=0, rate_per_minute=0,
                                           exempt={"admin-1"})
    chat_id = client.post("/chats", json={"owner_external_id": "user-1", "interface": "telegram"}).json()["chat_id"]

    assert client.post(f"/chats/{chat_id}/messages", data={"content": "вопрос 1"}).status_code == 200  # $0,6
    assert client.post(f"/chats/{chat_id}/messages", data={"content": "вопрос 2"}).status_code == 200  # $1,2
    blocked = client.post(f"/chats/{chat_id}/messages", data={"content": "вопрос 3"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert blocked.json()["detail"] == {"code": "daily_limit", "message": "Лимит на сегодня исчерпан, обновится в 00:00 МСК."}
    assert len(llm.calls) == 2  # третье сообщение до модели не дошло

    admin_chat = client.post("/chats", json={"owner_external_id": "admin-1", "interface": "telegram"}).json()["chat_id"]
    for i in range(3):  # админ без лимита
        assert client.post(f"/chats/{admin_chat}/messages", data={"content": f"админ {i}"}).status_code == 200
