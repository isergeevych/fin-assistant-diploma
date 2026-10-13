"""Б6.4 — HIL-граф на AsyncSqliteSaver(":memory:"): пауза, одобрение, отказ, роли. Без сети и Postgres."""

from unittest.mock import AsyncMock

import pytest
pytest.importorskip("langgraph", reason="нужна группа agents: uv sync --extra agents")
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.services import agent_persistent
from app.services.agent_persistent import build_agent

EMAIL_ARGS = {"to": "client@example.com", "subject": "Траты за сентябрь", "body": "Расходы: 93 501,01 ₽"}


class ScriptedModel:
    """Модель-сценарий: сначала просит отправить письмо, потом подводит итог."""

    def __init__(self, args=EMAIL_ARGS):
        self.replies = [AIMessage(content="", tool_calls=[{"name": "send_report_email", "args": args, "id": "call_1"}]),
                        AIMessage(content="Готово.")]

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def memory_ledger(monkeypatch):
    monkeypatch.setattr(agent_persistent, "_ledger", agent_persistent.EmailLedger(":memory:"))


@pytest.fixture
async def graph(monkeypatch):
    sender = AsyncMock()
    monkeypatch.setattr(agent_persistent, "send_email", sender)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        await saver.setup()
        yield build_agent(saver, ScriptedModel()), sender


def _config(thread: str, role: str = "write-with-approve") -> dict:
    return {"configurable": {"thread_id": thread, "user_role": role}}


async def _run_to_interrupt(g, thread: str):
    cfg = _config(thread)
    result = await g.ainvoke({"messages": [{"role": "user", "content": "Отправь отчёт на client@example.com"}],
                              "iteration_count": 0}, cfg)
    return cfg, result


async def test_pauses_on_interrupt_before_side_effect(graph):
    g, sender = graph
    cfg, result = await _run_to_interrupt(g, "t-pause")
    payload = result["__interrupt__"][0].value
    assert payload["type"] == "approve_send_report_email" and payload["preview"]["to"] == "client@example.com"
    snapshot = await g.aget_state(cfg)
    assert snapshot.next == ("confirm_and_execute_email",)
    assert snapshot.values["email_draft"]["id"] == "t-pause:call_1"  # детерминированный id, без uuid
    sender.assert_not_called()  # до решения человека ничего не ушло


async def test_resume_true_sends_once(graph):
    g, sender = graph
    cfg, _ = await _run_to_interrupt(g, "t-yes")
    result = await g.ainvoke(Command(resume=True), cfg)
    assert result["sent"] is True and result["messages"][-1].content == "Готово."
    sender.assert_awaited_once()
    assert sender.await_args.args[0]["to"] == "client@example.com"


async def test_resume_false_does_not_send(graph):
    g, sender = graph
    cfg, _ = await _run_to_interrupt(g, "t-no")
    result = await g.ainvoke(Command(resume=False), cfg)
    assert result["sent"] is False
    sender.assert_not_called()
    assert "отклонил" in [m for m in result["messages"] if m.type == "tool"][-1].content


@pytest.mark.parametrize(("role", "sent", "calls"), [("read-only", False, 0), ("full", True, 1)])
async def test_role_policy(monkeypatch, role, sent, calls):
    sender = AsyncMock()
    monkeypatch.setattr(agent_persistent, "send_email", sender)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        g = build_agent(saver, ScriptedModel())
        result = await g.ainvoke({"messages": [{"role": "user", "content": "отправь"}], "iteration_count": 0},
                                 _config(f"t-{role}", role))
    assert "__interrupt__" not in result and result["sent"] is sent and sender.await_count == calls


async def test_invalid_address_is_not_drafted(monkeypatch):
    sender = AsyncMock()
    monkeypatch.setattr(agent_persistent, "send_email", sender)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        g = build_agent(saver, ScriptedModel({**EMAIL_ARGS, "to": "не-адрес"}))
        result = await g.ainvoke({"messages": [{"role": "user", "content": "отправь"}], "iteration_count": 0},
                                 _config("t-bad"))
    assert "__interrupt__" not in result and result["email_draft"] is None
    sender.assert_not_called()


async def test_replay_from_paused_checkpoint_does_not_send_twice(graph):
    # time_travel_demo: replay со старого чек-пойнта паузы повторял узел после interrupt() → второе письмо
    g, sender = graph
    cfg, _ = await _run_to_interrupt(g, "t-replay")
    paused_id = (await g.aget_state(cfg)).config["configurable"]["checkpoint_id"]
    await g.ainvoke(Command(resume=True), cfg)
    replay_cfg = {"configurable": {**cfg["configurable"], "checkpoint_id": paused_id}}
    replay_graph = build_agent(g.checkpointer, ScriptedModel())  # модель-сценарий одноразовая — новая для replay
    result = await replay_graph.ainvoke(Command(resume=True), replay_cfg)
    sender.assert_awaited_once()  # журнал отправок не дал повторить side-effect
    assert "уже было отправлено" in [m for m in result["messages"] if m.type == "tool"][-1].content
