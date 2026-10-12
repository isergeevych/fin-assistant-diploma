"""
Б6.4 — time travel по чек-пойнтам HIL-графа. Офлайн: AsyncSqliteSaver(":memory:"), модель-сценарий, отправка —
print вместо SMTP. Сеть и ключи не нужны.

    python -m scripts.time_travel_demo
"""

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.services import agent_persistent
from app.services.agent_persistent import build_agent

INPUT = {"messages": [{"role": "user", "content": "Отправь отчёт о тратах за сентябрь на client@example.com"}],
         "iteration_count": 0}
SENT: list[str] = []


class DemoModel:
    """Без состояния: на сообщение пользователя просит отправить письмо, после ответа tool — подводит итог."""

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(content="Готовлю письмо с отчётом.", tool_calls=[{
                "name": "send_report_email", "id": "call_1",
                "args": {"to": "client@example.com", "subject": "Траты за сентябрь 2026",
                         "body": "Расходы за сентябрь: 93 501,01 ₽. Крупнейшая категория — жильё."}}])
        return AIMessage(content=f"Итог: {messages[-1].content}")


async def fake_send(draft: dict) -> None:
    SENT.append(draft["id"])
    print(f"    [SMTP] письмо {draft['id']} → {draft['to']}")


def cfg(thread: str, checkpoint_id: str | None = None) -> dict:
    conf = {"thread_id": thread, "user_role": "write-with-approve"}
    return {"configurable": {**conf, "checkpoint_id": checkpoint_id} if checkpoint_id else conf}


async def main() -> None:
    agent_persistent.send_email = fake_send
    agent_persistent._ledger = agent_persistent.EmailLedger(":memory:")  # журнал отправок — в памяти, офлайн
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        await saver.setup()
        graph = build_agent(saver, DemoModel())

        print("1) Запуск до interrupt (thread demo-approve)")
        result = await graph.ainvoke(INPUT, cfg("demo-approve"))
        print("   __interrupt__ payload:", json.dumps(result["__interrupt__"][0].value, ensure_ascii=False, indent=2)
              .replace("\n", "\n   "))

        print("\n2) История чек-пойнтов (новые сверху)")
        history = [s async for s in graph.aget_state_history(cfg("demo-approve"))]
        print(f"   {'checkpoint_id':<38} {'step':>4}  next")
        for s in history:
            print(f"   {s.config['configurable']['checkpoint_id']:<38} {s.metadata.get('step'):>4}  {s.next}")
        paused = history[0]

        print("\n3) Состояние на прошлом чек-пойнте (до отправки)")
        past = await graph.aget_state(cfg("demo-approve", paused.config["configurable"]["checkpoint_id"]))
        print(f"   next={past.next}  sent={past.values.get('sent', False)}  draft.subject={past.values['email_draft']['subject']!r}")

        print("\n4) Две ветки из одинакового входа (разные thread_id)")
        approve = await graph.ainvoke(Command(resume=True), cfg("demo-approve"))
        print(f"   demo-approve: resume=True  → sent={approve['sent']}   «{approve['messages'][-1].content[:70]}»")
        await graph.ainvoke(INPUT, cfg("demo-reject"))
        reject = await graph.ainvoke(Command(resume=False), cfg("demo-reject"))
        print(f"   demo-reject:  resume=False → sent={reject['sent']}  «{reject['messages'][-1].content[:70]}»")
        print(f"   отправлено писем: {len(SENT)} ({SENT})")

        print("\n5) Нюанс: повторный resume ТОГО ЖЕ чек-пойнта паузы с другим решением")
        replay = await graph.ainvoke(Command(resume=False), cfg("demo-approve", paused.config["configurable"]["checkpoint_id"]))
        print(f"   demo-approve @ {paused.config['configurable']['checkpoint_id'][:13]}…: resume=False → sent={replay.get('sent')}")
        print(f"   отправлено писем всего: {len(SENT)} ({SENT})")


if __name__ == "__main__":
    asyncio.run(main())
