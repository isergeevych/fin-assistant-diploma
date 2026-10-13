"""Б6.3 — custom StateGraph без сети: router как чистая функция, стоп-кран на «бесполезном» tool."""

import pytest
pytest.importorskip("langgraph", reason="нужна группа agents: uv sync --extra agents")
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services import agent_graph
from app.services.agent_graph import MAX_ITERATIONS, custom_graph, initial_state, route_after_model


def _ai(tool_calls=None, content=""):
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_router_is_pure_and_typed():
    call = [{"name": "get_current_time", "args": {}, "id": "c1"}]
    state = {"messages": [HumanMessage("x"), _ai(call)], "iteration_count": 2, "tool_results": []}
    snapshot = repr(state)
    assert route_after_model(state) == "execute_tool"
    assert route_after_model({**state, "iteration_count": MAX_ITERATIONS}) == "force_finish"
    assert route_after_model({**state, "messages": [HumanMessage("x"), _ai(content="ответ")]}) == "force_finish"
    assert repr(state) == snapshot  # router ничего не пишет в state


class AlwaysToolModel:
    """Модель, которая никогда не останавливается: на каждый шаг снова просит tool."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return _ai([{"name": "search_knowledge_base", "args": {"query": "ещё раз"}, "id": f"c{self.calls}"}])


async def test_stop_crane_on_useless_tool(monkeypatch):
    fake = AlwaysToolModel()
    monkeypatch.setattr(agent_graph, "model_with_tools", fake)
    monkeypatch.setitem(agent_graph.TOOLS_BY_NAME, "search_knowledge_base", None)  # «сломанный» tool → строка-ошибка
    result = await custom_graph.ainvoke(initial_state("найди то, чего нет"))
    assert fake.calls == MAX_ITERATIONS and result["iteration_count"] == MAX_ITERATIONS
    assert result["messages"][-1].content.startswith(f"Не успел решить задачу за {MAX_ITERATIONS} шагов")
    # у каждого tool_call есть ответ — иначе следующий запрос в этот thread API отвергнет
    asked = {tc["id"] for m in result["messages"] if isinstance(m, AIMessage) for tc in m.tool_calls}
    answered = {m.tool_call_id for m in result["messages"] if isinstance(m, ToolMessage)}
    assert asked == answered
    assert len(result["tool_results"]) == MAX_ITERATIONS - 1  # operator.add копит результаты всех шагов


async def test_unknown_tool_does_not_crash(monkeypatch):
    responses = iter([_ai([{"name": "get_user_balance", "args": {"user": "777"}, "id": "c1"}]), _ai(content="нет такого")])

    class Fake:
        async def ainvoke(self, messages):
            return next(responses)

    monkeypatch.setattr(agent_graph, "model_with_tools", Fake())
    result = await custom_graph.ainvoke(initial_state("проверь баланс"))
    tool_msg = [m for m in result["messages"] if isinstance(m, ToolMessage)][0]
    assert "инструмента 'get_user_balance' нет" in tool_msg.content and result["messages"][-1].content == "нет такого"
