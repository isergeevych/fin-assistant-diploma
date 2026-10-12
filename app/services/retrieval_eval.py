"""
Б5.4 — метрики retrieval на уровне ДОКУМЕНТОВ.

Поиск возвращает чанки; несколько чанков одного документа схлопываются в одну позицию ранжирования
(иначе документ из трёх чанков «занял» бы три места, а метрики завысились бы).

  HitRate@5  — доля вопросов, где хотя бы один эталонный документ попал в top-5;
  MRR@10     — среднее 1/rank первого эталонного документа в top-10 (0, если не нашёлся);
  Recall@10  — средняя доля эталонных документов, найденных в top-10 (важно для многошаговых вопросов).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


def dedupe(sources: Sequence[str]) -> list[str]:
    seen, out = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def hit_rate(ranked: list[str], relevant: set[str], k: int = 5) -> float:
    return 1.0 if any(d in relevant for d in ranked[:k]) else 0.0


def reciprocal_rank(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    for i, d in enumerate(ranked[:k], 1):
        if d in relevant:
            return 1.0 / i
    return 0.0


def recall(ranked: list[str], relevant: set[str], k: int = 10) -> float:
    return len(relevant & set(ranked[:k])) / len(relevant) if relevant else 0.0


@dataclass
class RetrievalReport:
    hit_rate_at_5: float
    mrr_at_10: float
    recall_at_10: float
    per_question: list[dict]


def evaluate(dataset: list[dict], retrieve: Callable[[str], list[str]]) -> RetrievalReport:
    """retrieve(question) → список source в порядке ранжирования (чанки, ещё не схлопнутые)."""
    rows = []
    for item in dataset:
        ranked = dedupe(retrieve(item["question"]))
        rel = set(item["relevant_doc_ids"])
        rows.append({"id": item["id"], "type": item.get("type"), "hit@5": hit_rate(ranked, rel),
                     "rr@10": reciprocal_rank(ranked, rel), "recall@10": recall(ranked, rel), "top3": ranked[:3]})
    n = len(rows) or 1
    return RetrievalReport(
        hit_rate_at_5=round(sum(r["hit@5"] for r in rows) / n, 3),
        mrr_at_10=round(sum(r["rr@10"] for r in rows) / n, 3),
        recall_at_10=round(sum(r["recall@10"] for r in rows) / n, 3),
        per_question=rows,
    )
