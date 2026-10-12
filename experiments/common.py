"""
Б6.5 — общее для обоих экспериментов: вопросы, ОДИН И ТОТ ЖЕ tool, модель и честный учёт затрат.

Токены и число LLM-вызовов считаются callback'ом на КАЖДЫЙ вызов модели, а не по сообщениям в итоговом
state: у supervisor с output_mode="last_message" промежуточные шаги researcher'а в state не попадают, и подсчёт
по сообщениям занизил бы стоимость мультиагента.
"""

import json
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool

from app.services import agent_naive
from app.services.agent_graph import build_model

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results.json"

QUESTIONS = [
    ("q1_corpus", "корпус", "Сколько максимум можно вернуть за лечение зубов за год?"),
    ("q2_corpus", "корпус", "До какой суммы АСВ страхует вклады и когда начинаются выплаты?"),
    ("q3_corpus", "корпус", "Что делать, если звонят из «службы безопасности банка» и просят код из SMS?"),
    ("q4_multi", "многошаговый",
     "Сравни вычет по ИИС-3 и вычет за своё обучение: сколько максимум можно вернуть по каждому за год "
     "и можно ли получить оба сразу?"),
    ("q5_out", "вне базы", "Как оформить загранпаспорт нового образца?"),
]

CAPTURED: list[dict] = []  # что вернул tool за текущий вопрос — контексты для оценки faithfulness


@tool
def search_knowledge_base(query: str) -> str:
    """Ищет в базе знаний Финпомощника (налоги и вычеты, вклады и кредиты, бюджет, мошенничество, FAQ приложения)
    и возвращает самый релевантный фрагмент с именем файла в квадратных скобках. Если вернулось «ничего не
    найдено» — в базе ответа нет, не выдумывай."""
    result = agent_naive.search_knowledge_base(query)
    CAPTURED.append({"query": query, "result": result})
    return result


class UsageCounter(BaseCallbackHandler):
    """Считает каждый вызов модели и его токены — включая роутинг supervisor'а и внутренние шаги агентов."""

    def __init__(self):
        self.llm_calls = self.input_tokens = self.output_tokens = 0

    def on_llm_end(self, response, **kwargs):
        self.llm_calls += 1
        for gen in (g for batch in response.generations for g in batch):
            usage = getattr(getattr(gen, "message", None), "usage_metadata", None) or {}
            self.input_tokens += usage.get("input_tokens", 0)
            self.output_tokens += usage.get("output_tokens", 0)


def model():
    return build_model()


def save(impl: str, rows: list[dict]) -> None:
    data = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else []
    data = [r for r in data if r["impl"] != impl] + rows
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record(impl: str, qid: str, kind: str, question: str, answer: str, counter: UsageCounter, started: float,
           handoffs: int) -> dict:
    return {"impl": impl, "question_id": qid, "kind": kind, "question": question, "answer": answer,
            "total_tokens": counter.input_tokens + counter.output_tokens, "input_tokens": counter.input_tokens,
            "output_tokens": counter.output_tokens, "llm_calls": counter.llm_calls,
            "latency_ms": round((time.perf_counter() - started) * 1000), "handoff_count": handoffs,
            "contexts": [c["result"] for c in CAPTURED], "tool_calls": len(CAPTURED)}
