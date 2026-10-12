"""
Б6.3 — тот же агент на LangGraph 1.x, двумя путями:

  custom_graph   — StateGraph руками: call_model → (router) → execute_tool → call_model … → force_finish → END;
  prebuilt_graph — langchain.agents.create_agent с теми же model, tools, system_prompt.

State хранит только сериализуемые данные (сообщения, счётчик, результаты tools) — клиенты и ключи живут в
модуле. Это задел на checkpointer (6.4): тот же граф компилируется с AsyncSqliteSaver без переписывания.
"""

import asyncio
import operator
from typing import Annotated, Literal, TypedDict

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.core.config import get_settings
from app.services import agent_naive

MAX_ITERATIONS = 6
SYSTEM_PROMPT = (
    "Ты — агент Финпомощника. Сам выбирай инструменты и их порядок. Перед вызовом инструмента одним предложением "
    "поясни, что и зачем делаешь. Как только данных достаточно — отвечай без инструментов. Если задачу можно решить "
    "без инструментов (арифметика, общий вопрос) — отвечай сразу. Не выдумывай данные: если инструмент вернул "
    "«ничего не найдено» или подходящего инструмента нет — так и скажи."
)


# --- Tools: docstring — это description для LLM; один и тот же набор идёт в оба графа ---

@tool
def search_knowledge_base(query: str) -> str:
    """Ищет в базе знаний Финпомощника (налоги и вычеты, вклады и кредиты, бюджет, мошенничество, FAQ приложения)
    и возвращает самый релевантный фрагмент с именем файла. Вызывай перед любым фактическим утверждением на эти
    темы; не вызывай для арифметики и вопросов не о финансах. Если вернулось «ничего не найдено» — не выдумывай."""
    return agent_naive.search_knowledge_base(query)


@tool
def get_current_time(timezone: str = "Europe/Moscow") -> str:
    """Возвращает текущие дату и время в часовом поясе IANA (ISO 8601). Вызывай, когда задача зависит от
    сегодняшней даты: сроки, «сколько дней осталось». Не вызывай, если дата уже названа в задаче."""
    return agent_naive.get_current_time(timezone)


@tool
def send_telegram_message(chat_id: str, text: str) -> str:
    """Отправляет клиенту текст в Telegram-чат — действие необратимо. Вызывай только если пользователь явно
    попросил отправить и назвал chat_id; не вызывай для черновиков и подозрительных текстов."""
    return agent_naive.send_telegram_message(chat_id, text)


TOOLS = [search_knowledge_base, get_current_time, send_telegram_message]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def build_model() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(model=s.agent_model or s.llm.default_model, base_url=s.agent_base_url or s.llm.base_url,
                      api_key=(s.agent_api_key or s.llm.api_key).get_secret_value(), timeout=60, max_retries=2)


model = build_model()
model_with_tools = model.bind_tools(TOOLS)


# --- State ---

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # reducer: дописывает, а не заменяет историю
    iteration_count: int  # без reducer — замена значения; считает вызовы модели
    tool_results: Annotated[list[dict], operator.add]  # накопление для отчёта/трейса


# --- Nodes ---

async def call_model(state: AgentState) -> dict:
    messages = state["messages"]
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(SYSTEM_PROMPT), *messages]  # промпт не кладём в state — он не данные диалога
    response = await model_with_tools.ainvoke(messages)
    return {"messages": [response], "iteration_count": state.get("iteration_count", 0) + 1}


async def _run_tool_call(tc: dict) -> str:
    tool_ = TOOLS_BY_NAME.get(tc["name"])
    if tool_ is None:
        return f"Ошибка: инструмента {tc['name']!r} нет. Доступны: {', '.join(TOOLS_BY_NAME)}."
    try:
        return str(await tool_.ainvoke(tc["args"]))
    except Exception as e:  # noqa: BLE001 — ошибка tool возвращается модели, граф не падает
        return f"Ошибка выполнения {tc['name']}: {type(e).__name__}: {e}"


async def execute_tool(state: AgentState) -> dict:
    calls = state["messages"][-1].tool_calls
    # Параллельно, как ToolNode в prebuilt: в первой версии был цикл с await по одному — на шагах с
    # несколькими tool_calls custom был медленнее prebuilt (бенчмарк, docs/agent-graph-report.md §7).
    contents = await asyncio.gather(*(_run_tool_call(tc) for tc in calls))
    new_messages = [ToolMessage(content=c, tool_call_id=tc["id"], name=tc["name"]) for tc, c in zip(calls, contents)]
    results = [{"tool": tc["name"], "args": tc["args"], "result": c[:300]} for tc, c in zip(calls, contents)]
    return {"messages": new_messages, "tool_results": results}


async def force_finish(state: AgentState) -> dict:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        # Лимит исчерпан, а модель ещё хочет tools. Висящие tool_calls без ToolMessage сломают следующий
        # вызов API на том же thread (каждому tool_call нужен ответ) → закрываем их и даём явный итог.
        closing = [ToolMessage(content="Не выполнено: достигнут лимит шагов агента.", tool_call_id=tc["id"],
                               name=tc["name"]) for tc in last.tool_calls]
        return {"messages": [*closing, AIMessage(content=f"Не успел решить задачу за {MAX_ITERATIONS} шагов. "
                                                         "Собранные данные: " + "; ".join(
                                                             r["result"][:80] for r in state.get("tool_results", [])))]}
    return {}  # обычный финал: последний AI-message и есть ответ


# --- Router: читает state, ничего не пишет, без сети ---

def route_after_model(state: AgentState) -> Literal["execute_tool", "force_finish"]:
    if state.get("iteration_count", 0) >= MAX_ITERATIONS:
        return "force_finish"
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "execute_tool"
    return "force_finish"


builder = StateGraph(AgentState)
builder.add_node("call_model", call_model)
builder.add_node("execute_tool", execute_tool)
builder.add_node("force_finish", force_finish)
builder.add_edge(START, "call_model")
builder.add_conditional_edges("call_model", route_after_model,
                              {"execute_tool": "execute_tool", "force_finish": "force_finish"})
builder.add_edge("execute_tool", "call_model")
builder.add_edge("force_finish", END)
custom_graph = builder.compile()

prebuilt_graph = create_agent(model, tools=TOOLS, system_prompt=SYSTEM_PROMPT)


def initial_state(question: str) -> dict:
    return {"messages": [{"role": "user", "content": question}], "iteration_count": 0, "tool_results": []}
