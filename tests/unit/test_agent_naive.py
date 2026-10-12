"""Б6.1 — цикл наивного агента на поддельном клиенте: без сети, LLM и Qdrant."""

from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageFunctionToolCall
from openai.types.chat.chat_completion_message_function_tool_call import Function

from app.services import agent_naive


def _call(name: str, args: str, cid: str = "c1") -> ChatCompletionMessageFunctionToolCall:
    return ChatCompletionMessageFunctionToolCall(id=cid, type="function", function=Function(name=name, arguments=args))


def _resp(content=None, tool_calls=None):
    msg = ChatCompletionMessage(role="assistant", content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10))


class FakeClient:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append([dict(m) if isinstance(m, dict) else m for m in kwargs["messages"]])
        return self.responses.pop(0)


@pytest.fixture
def fake(monkeypatch):
    def install(*responses):
        client = FakeClient(responses)
        monkeypatch.setattr(agent_naive, "OpenAI", lambda **_: client)
        return client
    return install


def test_tool_then_answer(fake, capsys):
    client = fake(_resp(tool_calls=[_call("send_telegram_message", '{"chat_id": "12345", "text": "Привет"}')]),
                  _resp(content="Отправил."))
    out = agent_naive.run_agent("Отправь привет в чат 12345")
    assert out["answer"] == "Отправил." and out["steps"] == 2 and "error" not in out
    assert "[TELEGRAM → 12345] Привет" in capsys.readouterr().out
    tool_msg = client.calls[1][-1]
    assert tool_msg == {"role": "tool", "tool_call_id": "c1", "content": "Сообщение отправлено в 12345"}
    first = out["trace"][0]
    assert first["tool_name"] == "send_telegram_message" and first["llm_input_tokens"] == 100
    assert set(first) >= {"step", "tool_args", "tool_result", "llm_output_tokens", "duration_ms"}


def test_unknown_tool_is_error_string_not_crash(fake):
    client = fake(_resp(tool_calls=[_call("get_user_balance", '{"user_id": "1"}')]), _resp(content="Такого инструмента нет."))
    out = agent_naive.run_agent("Проверь баланс")
    assert out["answer"] == "Такого инструмента нет."
    assert client.calls[1][-1]["content"].startswith("Ошибка: инструмента get_user_balance нет")


def test_bad_arguments_go_back_to_model(fake):
    client = fake(_resp(tool_calls=[_call("get_current_time", '{"timezone": "Mars/Olympus"}')]), _resp(content="ok"))
    agent_naive.run_agent("Который час на Марсе?")
    assert client.calls[1][-1]["content"].startswith("Ошибка выполнения get_current_time")


def test_max_steps_stops_loop(fake):
    fake(*[_resp(tool_calls=[_call("get_current_time", "{}", cid=f"c{i}")]) for i in range(3)])
    out = agent_naive.run_agent("зациклись", max_steps=3)
    assert out["answer"] is None and out["steps"] == 3 and "max_steps=3" in out["error"]
    assert len(out["trace"]) == 3


def test_no_dynamic_dispatch_in_source():
    src = open(agent_naive.__file__, encoding="utf-8").read()
    for banned in ("eval(", "exec(", "getattr(", "globals()["):
        assert banned not in src
