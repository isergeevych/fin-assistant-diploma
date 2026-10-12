"""
Б6.2 — 5 задач × 2 агента (agent_naive baseline vs agent_react) → docs/agent-react-runs.json.

    python -m scripts.compare_agents

Корректность ответов оценивается руками по критерию в CHECKS (видно в отчёте docs/agent-react-report.md).
"""

import json
import time
from pathlib import Path

from app.services.agent_naive import run_agent, search_knowledge_base
from app.services.agent_react import run_react_with_reflection

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "agent-react-runs.json"

TASKS = [
    ("simple_time", "простая (1 tool)", "Какое сегодня число по московскому времени?"),
    ("simple_kb", "простая (1 tool)", "Сколько максимум можно вернуть за лечение зубов за год?"),
    ("medium_send", "средняя: выход search → вход send",
     "Найди в базе, на какую максимальную сумму АСВ страхует вклады, и отправь эту сумму одной фразой в чат 4242."),
    ("medium_days", "средняя: дата + база → расчёт",
     "До какого числа нужно заплатить налог на проценты по вкладам за прошлый год? Посчитай, сколько дней "
     "осталось с сегодняшнего дня."),
    ("provocation", "провокация: tools не нужны", "Сколько будет 15 % от 80 000 ₽? Ответь числом."),
]


def main() -> None:
    search_knowledge_base("прогрев")  # загрузка индекса и re-ranker (~200 с в первый раз) — не в зачёт таймаутов
    results = []
    for slug, kind, task in TASKS:
        print(f"\n===== {slug} ({kind}): {task}")
        t0 = time.perf_counter()
        naive = run_agent(task)
        naive_ms = round((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        react = run_react_with_reflection(task)
        react_ms = round((time.perf_counter() - t0) * 1000)
        row = {
            "task": slug, "kind": kind, "question": task,
            "naive": {"iterations": naive["steps"], "tools": [t["tool_name"] for t in naive["trace"] if t["tool_name"]],
                      "tokens": sum(t["llm_input_tokens"] + t["llm_output_tokens"] for t in naive["trace"]),
                      "latency_ms": naive_ms, "answer": naive["answer"], "error": naive.get("error")},
            "react": {"iterations": react["iterations"], "tools": [t["tool_name"] for t in react["trace"] if t["tool_name"]],
                      "tokens": react["usage"]["total"], "latency_ms": react_ms, "revisions": react["revisions_used"],
                      "critic": [t["critic"]["verdict"] for t in react["trace"] if t["critic"]],
                      "answer": react["answer"], "error": react.get("error"), "stop_reason": react["stop_reason"]},
        }
        for name in ("naive", "react"):
            r = row[name]
            print(f"  {name:<5} it={r['iterations']} tools={r['tools']} tokens={r['tokens']} {r['latency_ms']} мс")
            print(f"        → {(r['answer'] or r['error'] or '')[:300]}")
        print(f"  critic: {row['react']['critic']}")
        results.append(row)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
