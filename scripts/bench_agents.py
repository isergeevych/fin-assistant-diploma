"""
Б6.3 — 5 задач × 3 реализации × N повторов: ReAct-цикл Б6.2 (agent_react, с критиком), custom StateGraph,
prebuilt create_agent. Латентность — wall-clock, токены — usage каждого LLM-вызова (у Б6.2 — включая критика).

    python -m scripts.bench_agents --repeats 3

Результат: docs/agent-graph-bench.json (все прогоны) и таблица средних в stdout (→ docs/agent-graph-report.md).
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from langchain_core.messages import AIMessage

from app.services.agent_graph import custom_graph, initial_state, prebuilt_graph
from app.services.agent_naive import search_knowledge_base
from app.services.agent_react import run_react_with_reflection
from scripts.compare_agents import TASKS

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent-graph-bench.json"


def _graph_usage(messages: list) -> tuple[int, int]:
    ai = [m for m in messages if isinstance(m, AIMessage) and m.usage_metadata]
    return sum(m.usage_metadata["input_tokens"] for m in ai), sum(m.usage_metadata["output_tokens"] for m in ai)


async def run_one(impl: str, slug: str, question: str) -> dict:
    started = time.perf_counter()
    if impl == "react_6_2":
        r = await asyncio.to_thread(run_react_with_reflection, question)
        prompt, completion, steps, answer = r["usage"]["prompt"], r["usage"]["completion"], r["iterations"], r["answer"]
    elif impl == "custom":
        r = await custom_graph.ainvoke(initial_state(question), config={"configurable": {"thread_id": f"bench-{slug}"}})
        (prompt, completion), steps, answer = _graph_usage(r["messages"]), r["iteration_count"], r["messages"][-1].content
    else:
        r = await prebuilt_graph.ainvoke({"messages": [{"role": "user", "content": question}]})
        (prompt, completion) = _graph_usage(r["messages"])
        steps = sum(isinstance(m, AIMessage) for m in r["messages"])
        answer = r["messages"][-1].content
    return {"impl": impl, "task": slug, "latency_ms": round((time.perf_counter() - started) * 1000),
            "prompt_tokens": prompt, "completion_tokens": completion, "steps": steps, "answer": answer}


async def main_async(repeats: int) -> None:
    search_knowledge_base("прогрев")  # индекс + re-ranker (~200 с в первый раз) — вне замеров
    runs = []
    for rep in range(1, repeats + 1):
        for slug, _, question in TASKS:
            for impl in ("react_6_2", "custom", "prebuilt"):
                row = await run_one(impl, slug, question)
                runs.append({**row, "repeat": rep})
                print(f"  r{rep} {slug:<12} {impl:<9} {row['latency_ms']:>6} мс  "
                      f"{row['prompt_tokens']:>5}+{row['completion_tokens']:<4} шагов={row['steps']}")
    OUT.write_text(json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n| Задача | Реализация | latency, мс | prompt_tokens | completion_tokens | шагов |")
    print("|---|---|---|---|---|---|")
    for slug, _, _ in TASKS:
        for impl in ("react_6_2", "custom", "prebuilt"):
            rows = [r for r in runs if r["task"] == slug and r["impl"] == impl]
            m = {k: statistics.mean(r[k] for r in rows) for k in ("latency_ms", "prompt_tokens", "completion_tokens", "steps")}
            print(f"| {slug} | {impl} | {m['latency_ms']:.0f} | {m['prompt_tokens']:.0f} | {m['completion_tokens']:.0f} | "
                  f"{m['steps']:.1f} |")
    print("\n| Реализация | latency, мс (сумма 5 задач) | prompt | completion | total |")
    print("|---|---|---|---|---|")
    for impl in ("react_6_2", "custom", "prebuilt"):
        rows = [r for r in runs if r["impl"] == impl]
        lat = sum(r["latency_ms"] for r in rows) / repeats
        p, c = sum(r["prompt_tokens"] for r in rows) / repeats, sum(r["completion_tokens"] for r in rows) / repeats
        print(f"| {impl} | {lat:.0f} | {p:.0f} | {c:.0f} | {p + c:.0f} |")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    asyncio.run(main_async(parser.parse_args().repeats))


if __name__ == "__main__":
    main()
