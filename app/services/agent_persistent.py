"""
Б6.4 — персистентный ReAct-граф с human-in-the-loop. agent_graph.py (6.3) не тронут — in-memory вариант.

Опасный tool — send_report_email (письмо клиенту необратимо). Модель его только «запрашивает»; вместо
выполнения граф идёт в два отдельных узла:

  prepare_email             — ИДЕМПОТЕНТНО: валидирует аргументы, собирает черновик, id письма —
                              детерминированный (thread_id + tool_call_id), без uuid; side-effect'ов нет;
  confirm_and_execute_email — interrupt({"type": "approve_send_report_email", "preview": …}) → ждём человека;
                              ПОСЛЕ resume=True — реальная отправка по SMTP, resume=False — отказ без отправки.

При resume LangGraph перезапускает узел С НАЧАЛА, поэтому до interrupt() — только чтение state: если бы
отправка стояла до interrupt, письмо ушло бы дважды (при паузе и при продолжении).

Checkpointer выбирается AGENT_CHECKPOINTER (memory / sqlite / postgres) в agent_lifespan(); setup() —
ровно один раз при старте приложения. Роль пользователя — config["configurable"]["user_role"].
"""

import asyncio
import operator
import re
import smtplib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from email.message import EmailMessage
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from app.core.config import Settings, get_settings
from app.services import agent_graph

MAX_ITERATIONS = 8
EMAIL_TOOL = "send_report_email"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SYSTEM_PROMPT = agent_graph.SYSTEM_PROMPT + (
    " Отправка письма — необратимое действие: вызывай send_report_email, только если пользователь явно попросил "
    "отправить и назвал адрес; перед отправкой человек подтверждает черновик."
)


@tool
def send_report_email(to: str, subject: str, body: str) -> str:
    """Отправляет письмо-отчёт клиенту на e-mail — действие необратимо. Вызывай только если пользователь явно
    попросил отправить и назвал адрес. Перед реальной отправкой человек подтвердит черновик."""
    raise RuntimeError("выполняется только через узлы prepare_email → confirm_and_execute_email")


TOOLS = [agent_graph.search_knowledge_base, agent_graph.get_current_time, send_report_email]
TOOLS_BY_NAME = {t.name: t for t in TOOLS if t.name != EMAIL_TOOL}


async def send_email_smtp(draft: dict) -> None:
    """Реальный side-effect. В тестах подменяется AsyncMock."""
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, draft["to"], draft["subject"]
    msg["Message-ID"] = f"<{draft['id'].replace(':', '.')}@finpomoshnik.local>"  # один id на черновик
    msg.set_content(draft["body"])

    def _send() -> None:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.send_message(msg)

    await asyncio.to_thread(_send)


send_email: Callable[[dict], Awaitable[None]] = send_email_smtp


class EmailLedger:
    """Журнал отправленных писем ВНЕ графа (UNIQUE по id черновика).

    Найдено в time_travel_demo: replay со старого чек-пойнта паузы повторно выполняет узел после interrupt()
    с сохранённым решением — и письмо уходило второй раз. State тут не поможет: на старом чек-пойнте письмо
    ещё «не отправлено». Политика «не более одного раза»: сначала claim(), потом отправка; ошибка — release().
    """

    def __init__(self, path: str):
        import sqlite3

        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("CREATE TABLE IF NOT EXISTS sent_emails (draft_id TEXT PRIMARY KEY, "
                         "sent_at TEXT DEFAULT CURRENT_TIMESTAMP)")

    def claim(self, draft_id: str) -> bool:
        return self._db.execute("INSERT OR IGNORE INTO sent_emails (draft_id) VALUES (?)", (draft_id,)).rowcount == 1

    def release(self, draft_id: str) -> None:
        self._db.execute("DELETE FROM sent_emails WHERE draft_id = ?", (draft_id,))


_ledger: EmailLedger | None = None


def get_ledger() -> EmailLedger:
    global _ledger
    if _ledger is None:
        path = get_settings().agent_email_ledger_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _ledger = EmailLedger(str(path))
    return _ledger


class PersistentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    iteration_count: int
    tool_results: Annotated[list[dict], operator.add]
    email_draft: dict | None  # черновик ждёт подтверждения
    sent: bool  # последнее решение по письму: True — ушло, False — нет


def build_agent(checkpointer, model=None):
    """Фабрика графа. model — для офлайн-демо и тестов (нужны bind_tools/ainvoke)."""
    bound = (model or agent_graph.model).bind_tools(TOOLS, parallel_tool_calls=False)  # один tool за шаг

    async def call_model(state: PersistentState) -> dict:
        response = await bound.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response], "iteration_count": state.get("iteration_count", 0) + 1}

    async def execute_tool(state: PersistentState) -> dict:
        calls = state["messages"][-1].tool_calls
        contents = []
        for tc in calls:
            tool_ = TOOLS_BY_NAME.get(tc["name"])
            contents.append(str(await tool_.ainvoke(tc["args"])) if tool_ else f"Ошибка: инструмента {tc['name']!r} нет.")
        return {"messages": [ToolMessage(c, tool_call_id=tc["id"], name=tc["name"]) for tc, c in zip(calls, contents)],
                "tool_results": [{"tool": tc["name"], "args": tc["args"], "result": c[:300]} for tc, c in zip(calls, contents)]}

    async def prepare_email(state: PersistentState, config: RunnableConfig) -> dict:
        tc = state["messages"][-1].tool_calls[0]
        args, thread = tc["args"], config["configurable"]["thread_id"]
        if not EMAIL_RE.match(args.get("to", "")):
            return {"email_draft": None, "messages": [ToolMessage(f"Некорректный адрес: {args.get('to')!r}. Письмо не "
                                                                  "подготовлено.", tool_call_id=tc["id"], name=EMAIL_TOOL)]}
        draft = {"id": f"{thread}:{tc['id']}", "tool_call_id": tc["id"], "to": args["to"],
                 "subject": args["subject"].strip()[:150], "body": args["body"].strip()[:4000]}
        return {"email_draft": draft}

    async def confirm_and_execute_email(state: PersistentState, config: RunnableConfig) -> dict:
        draft = state["email_draft"]
        role = config["configurable"].get("user_role", get_settings().agent_default_role)
        if role == "read-only":
            approved, note = False, "Отправка запрещена для роли read-only."
        elif role == "full":
            approved, note = True, "Отправлено без подтверждения (роль full)."
        else:
            # всё выше — только чтение state: при resume узел выполнится заново с этой строки и выше
            decision = interrupt({"type": f"approve_{EMAIL_TOOL}", "preview": {k: draft[k] for k in ("to", "subject", "body")},
                                  "draft_id": draft["id"]})
            approved = decision is True or (isinstance(decision, dict) and decision.get("approve") is True)
            note = "Отправлено после подтверждения." if approved else "Пользователь отклонил отправку — письмо не ушло."
        if approved:  # side-effect — строго ПОСЛЕ interrupt() и не больше одного раза на черновик
            ledger = get_ledger()
            if ledger.claim(draft["id"]):
                try:
                    await send_email(draft)
                except Exception:
                    ledger.release(draft["id"])
                    raise
            else:
                note = "Это письмо уже было отправлено раньше — повторно не отправляю."
        result = f"{note} Кому: {draft['to']}, тема: «{draft['subject']}»."
        return {"email_draft": None, "sent": approved,
                "messages": [ToolMessage(result, tool_call_id=draft["tool_call_id"], name=EMAIL_TOOL)],
                "tool_results": [{"tool": EMAIL_TOOL, "args": {"to": draft["to"]}, "result": result}]}

    async def force_finish(state: PersistentState) -> dict:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:  # закрыть висящие tool_calls — иначе тред сломан (6.3, §7)
            return {"messages": [*(ToolMessage("Не выполнено: лимит шагов.", tool_call_id=tc["id"], name=tc["name"])
                                   for tc in last.tool_calls),
                                 AIMessage(f"Не успел решить задачу за {MAX_ITERATIONS} шагов.")]}
        return {}

    def route_after_model(state: PersistentState) -> Literal["execute_tool", "prepare_email", "force_finish"]:
        if state.get("iteration_count", 0) >= MAX_ITERATIONS:
            return "force_finish"
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "prepare_email" if last.tool_calls[0]["name"] == EMAIL_TOOL else "execute_tool"
        return "force_finish"

    def route_after_prepare(state: PersistentState) -> Literal["confirm_and_execute_email", "call_model"]:
        return "confirm_and_execute_email" if state.get("email_draft") else "call_model"

    builder = StateGraph(PersistentState)
    for name, fn in (("call_model", call_model), ("execute_tool", execute_tool), ("prepare_email", prepare_email),
                     ("confirm_and_execute_email", confirm_and_execute_email), ("force_finish", force_finish)):
        builder.add_node(name, fn)
    builder.add_edge(START, "call_model")
    builder.add_conditional_edges("call_model", route_after_model)
    builder.add_edge("execute_tool", "call_model")
    builder.add_conditional_edges("prepare_email", route_after_prepare)
    builder.add_edge("confirm_and_execute_email", "call_model")
    builder.add_edge("force_finish", END)
    return builder.compile(checkpointer=checkpointer)


def postgres_uri(settings: Settings) -> str:
    # тот же Postgres, что у чата (DATABASE_URL), но драйвер psycopg 3 вместо asyncpg
    return re.sub(r"^postgresql\+\w+://", "postgresql://", settings.database_url)


@asynccontextmanager
async def agent_lifespan(settings: Settings | None = None, model=None) -> AsyncIterator:
    """Поднимает checkpointer по AGENT_CHECKPOINTER, вызывает setup() ОДИН раз и отдаёт скомпилированный граф."""
    s = settings or get_settings()
    if s.agent_checkpointer == "memory":
        yield build_agent(InMemorySaver(), model)
    elif s.agent_checkpointer == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        s.agent_sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(s.agent_sqlite_path)) as saver:
            await saver.setup()
            yield build_agent(saver, model)
    else:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(postgres_uri(s)) as saver:
            await saver.setup()  # таблицы checkpoints*; Alembic их не трогает (include_name в alembic/env.py)
            yield build_agent(saver, model)
