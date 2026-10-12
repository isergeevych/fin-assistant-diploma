"""
Б6.5 — мультиагент: supervisor + researcher (ищет факты) + writer (пишет ответ с цитатами [1], [2]).

    python -m experiments.multi_agent_langgraph

Сборка — langgraph_supervisor.create_supervisor (а не ручной Command(goto=…)): для двух агентов с линейным
сценарием «найти → написать» готовый supervisor даёт handoff-tools и маршрутизацию без своего кода, а
сравнивается здесь не качество нашей реализации маршрутизации, а сама идея нескольких агентов.
"""

import asyncio
import time
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph_supervisor import create_supervisor

from experiments.common import CAPTURED, QUESTIONS, UsageCounter, model, record, save, search_knowledge_base

RESEARCHER_PROMPT = (
    "Ты — researcher. Собери факты по вопросу пользователя только через search_knowledge_base (при необходимости "
    "несколькими запросами). Твоё последнее сообщение ОБЯЗАТЕЛЬНО содержит маркированный список найденных фактов, "
    "у каждого — источник в квадратных скобках (имя файла): только этот список увидит writer, результаты поиска "
    "ему не передаются. Если база ничего не нашла — напиши «фактов не найдено». Связный ответ пользователю не пиши "
    "— это работа writer'а."
)
# v2: «Финальный ответ пользователю НЕ пиши» модель местами поняла как «не пиши ничего» — последнее сообщение
# researcher'а было пустым, при output_mode="last_message" факты терялись, и writer отвечал «в базе ответа нет»
# (q3, q4 при том, что поиск нашёл нужные документы).
WRITER_PROMPT = (
    "Ты — writer. Из списка фактов researcher'а собери связный ответ пользователю на русском, 2–5 предложений. "
    "Каждое утверждение — со ссылкой [1], [2] на источник, в конце — список источников. Не добавляй фактов, "
    "которых нет в списке. Если фактов нет — честно скажи, что в базе ответа нет."
)
SUPERVISOR_PROMPT = (
    "Ты — supervisor команды из двух агентов и сам НИКОГДА не пишешь ответ пользователю. Порядок всегда один: "
    "1) передай вопрос researcher'у; 2) когда researcher вернул факты — ОБЯЗАТЕЛЬНО передай их writer'у, даже если "
    "факты простые или их нет; 3) когда writer написал ответ — заверши работу, ничего не добавляя от себя."
)
# v1 промпта («сначала researcher, затем writer; сам не отвечай») supervisor нарушал в 4 из 5 вопросов: получал факты
# и отвечал сам, без writer'а и без цитат — experiments/results_v1_supervisor_skips_writer.json.


def build_app():
    llm = model()
    researcher = create_agent(llm, tools=[search_knowledge_base], system_prompt=RESEARCHER_PROMPT, name="researcher")
    writer = create_agent(llm, tools=[], system_prompt=WRITER_PROMPT, name="writer")
    workflow = create_supervisor([researcher, writer], model=llm, prompt=SUPERVISOR_PROMPT, output_mode="last_message")
    return workflow.compile(checkpointer=InMemorySaver())


def _is_handoff(m: AIMessage) -> bool:
    return str(m.content).startswith(("Transferring back to", "Successfully transferred"))


def final_answer(messages: list) -> tuple[str, str]:
    """(автор, текст) последнего содержательного AI-сообщения; служебные сообщения передачи управления пропускаем."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not _is_handoff(m):
            return getattr(m, "name", None) or "supervisor", m.content
    return "—", ""


async def main() -> None:
    app = build_app()
    rows = []
    for qid, kind, question in QUESTIONS:
        CAPTURED.clear()
        counter = UsageCounter()
        config = {"configurable": {"thread_id": f"exp-langgraph-{qid}"}, "callbacks": [counter]}
        print(f"\n===== {qid}: {question}")
        started = time.perf_counter()
        async for update in app.astream({"messages": [{"role": "user", "content": question}]}, config,
                                        stream_mode="updates"):
            for node in update:
                print(f"  · {node}")
        state = (await app.aget_state(config)).values
        handoffs = sum(1 for m in state["messages"] if isinstance(m, AIMessage)
                       for tc in m.tool_calls if tc["name"].startswith("transfer_"))
        author, answer = final_answer(state["messages"])
        row = record("multi_agent", qid, kind, question, answer, counter, started, handoffs)
        row["answered_by"] = author  # writer — как задумано; supervisor — пропустил writer
        print(f"  → {row['total_tokens']} токенов, {row['llm_calls']} LLM-вызовов, {row['latency_ms']} мс, "
              f"handoffs={handoffs}, ответил: {author}\n  {row['answer'][:400]}")
        rows.append(row)
    save("multi_agent", rows)
    mermaid = app.get_graph().draw_mermaid()
    (Path(__file__).resolve().parents[1] / "docs" / "architecture-multi-agent.md").write_text(
        "# Мультиагент (ДЗ 6.5): supervisor + researcher + writer\n\nСхема из `app.get_graph().draw_mermaid()` "
        "(`experiments/multi_agent_langgraph.py`).\n\n```mermaid\n" + mermaid + "```\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
