"""
Б6.5 — single-agent baseline: один create_agent с ТЕМ ЖЕ tool search_knowledge_base и промптом, объединяющим
роли researcher и writer.

    python -m experiments.single_agent_baseline
"""

import asyncio
import time

from langchain.agents import create_agent
from langchain_core.messages import AIMessage

from experiments.common import CAPTURED, QUESTIONS, UsageCounter, model, record, save, search_knowledge_base

PROMPT = (
    "Ты — ассистент Финпомощника. Сначала собери факты по вопросу только через search_knowledge_base (при "
    "необходимости несколькими запросами). Затем напиши связный ответ на русском, 2–5 предложений: каждое "
    "утверждение — со ссылкой [1], [2] на источник, в конце — список источников. Не добавляй фактов, которых нет "
    "в найденном. Если база ничего не нашла — честно скажи, что в базе ответа нет."
)


async def main() -> None:
    agent = create_agent(model(), tools=[search_knowledge_base], system_prompt=PROMPT)
    rows = []
    for qid, kind, question in QUESTIONS:
        CAPTURED.clear()
        counter = UsageCounter()
        print(f"\n===== {qid}: {question}")
        started = time.perf_counter()
        state = await agent.ainvoke({"messages": [{"role": "user", "content": question}]}, {"callbacks": [counter]})
        answer = next((m.content for m in reversed(state["messages"]) if isinstance(m, AIMessage) and m.content), "")
        row = record("single_agent", qid, kind, question, answer, counter, started, handoffs=0)
        print(f"  → {row['total_tokens']} токенов, {row['llm_calls']} LLM-вызовов, {row['latency_ms']} мс\n  {answer[:400]}")
        rows.append(row)
    save("single_agent", rows)


if __name__ == "__main__":
    asyncio.run(main())
