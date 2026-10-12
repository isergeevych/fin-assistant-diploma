"""Б6.2 — ReAct-агент на поддельном клиенте: лимиты, таймаут, ревизии, учёт токенов. Без сети и LLM."""

import time
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageFunctionToolCall
from openai.types.chat.chat_completion_message_function_tool_call import Function

from app.services import agent_react


def _resp(content=None, tool=None, args="{}", tokens=(100, 10)):
    calls = [ChatCompletionMessageFunctionToolCall(id="c1", type="function", function=Function(name=tool, arguments=args))] \
        if tool else None
    msg = ChatCompletionMessage(role="assistant", content=content, tool_calls=calls)
    usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1], total_tokens=sum(tokens))
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


class FakeClient:
    """Отвечает по очереди; запоминает модель каждого вызова (основной и critic идут через один клиент)."""

    def __init__(self, responses):
        self.responses, self.models = list(responses), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.models.append(kwargs["model"])
        return self.responses.pop(0)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(agent_react, "DISPATCH", {**agent_react.DISPATCH, "get_current_time": lambda timezone: "2026-10-13"})

    def install(*responses):
        client = FakeClient(responses)
        monkeypatch.setattr(agent_react, "OpenAI", lambda **_: client)
        return client
    return install


def test_answer_without_tools_on_provocation(fake):
    fake(_resp(content="12 000 ₽"))
    out = agent_react.run_react_with_reflection("Сколько будет 15% от 80 000 ₽?")
    assert out["answer"] == "12 000 ₽" and out["iterations"] == 1 and out["trace"][0]["tool_name"] is None


def test_critic_tokens_are_counted(fake):
    fake(_resp("узнаю дату", "get_current_time", '{"timezone": "Europe/Moscow"}', (100, 10)),
         _resp("OK", tokens=(50, 2)),  # critic
         _resp("Сегодня 13.10", tokens=(120, 8)))
    out = agent_react.run_react_with_reflection("Какое сегодня число?")
    assert out["usage"] == {"prompt": 270, "completion": 20, "total": 290}
    assert out["trace"][0]["critic"]["verdict"] == "OK"


def test_revisions_capped_at_two_and_not_reset(fake):
    tool = _resp("дата", "get_current_time", '{"timezone": "Europe/Moscow"}')
    revise = _resp("REVISE: не та зона")
    client = fake(tool, revise, tool, revise, tool, revise, _resp("готово"))
    out = agent_react.run_react_with_reflection("дата?", model_main="mini", model_premium="premium", model_critic="mini")
    assert out["revisions_used"] == 2  # третий REVISE не засчитан
    # после 1-го и 2-го REVISE генерация на премиум-модели, после 3-го (лимит исчерпан) — снова mini
    assert client.models == ["mini", "mini", "premium", "mini", "premium", "mini", "mini"]


def test_max_iterations_gives_explicit_message(fake):
    tool, ok = _resp("ещё раз", "get_current_time", '{"timezone": "Europe/Moscow"}'), _resp("OK")
    fake(*([tool, ok] * 3))
    out = agent_react.run_react_with_reflection("зациклись", max_iterations=3)
    assert out["answer"] is None and out["error"] == "Превышен лимит итераций (3)" and out["stop_reason"] == "max_iterations"


def test_slow_tool_gives_timeout(fake, monkeypatch):
    monkeypatch.setattr(agent_react, "DISPATCH", {**agent_react.DISPATCH, "get_current_time": lambda timezone: time.sleep(2)})
    fake(_resp("дата", "get_current_time", '{"timezone": "Europe/Moscow"}'))
    started = time.monotonic()
    out = agent_react.run_react_with_reflection("дата?", timeout_per_iteration_sec=0.3)
    assert out["stop_reason"] == "timeout" and out["error"].startswith("Timeout")
    assert time.monotonic() - started < 1.5  # не ждали медленный tool до конца


def test_limits_and_strict_schemas():
    with pytest.raises(ValueError):
        agent_react.run_react_with_reflection("x", max_iterations=30)
    for tool in agent_react.TOOLS:
        params = tool["function"]["parameters"]
        assert tool["function"]["strict"] is True and params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])  # strict требует все поля в required
        assert len(tool["function"]["description"].split(". ")) >= 4  # 4 вопроса чек-листа
