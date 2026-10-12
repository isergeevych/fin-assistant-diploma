"""
Б5.2 — эксперименты с Qdrant: cosine vs dot и три примера фильтров. Результаты → docs/vector_store_results.md

    python -m scripts.qdrant_experiments
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qdrant_client.models import DatetimeRange, Distance, FieldCondition, Filter, MatchValue

from app.core.config import get_settings
from app.services.embeddings import get_embedding_service
from app.services.vector_store import VectorStore
from scripts.load_to_qdrant import build_points, load

ROOT = Path(__file__).resolve().parents[1]
QUERIES = [
    "можно ли вернуть деньги за лечение зубов",
    "мне звонят из банка и просят код из смс",
    "сколько стоит подписка Плюс в приложении",
    "что будет с вкладом если у банка отзовут лицензию",
    "как погасить кредит раньше срока",
]
TODAY = datetime(2026, 10, 12, tzinfo=timezone.utc)  # дата эксперимента — фиксирована для воспроизводимости


def store(collection: str, distance: Distance = Distance.COSINE) -> VectorStore:
    s = get_settings()
    return VectorStore(s.qdrant_url, s.qdrant_api_key.get_secret_value(), collection, s.embedding_dim, distance)


def fmt(points) -> str:
    return "<br>".join(f"`{p.payload['source']}`#{p.payload['chunk_index']} ({p.score:.3f})" for p in points)


async def cosine_vs_dot(embedder) -> list[str]:
    points = build_points(embedder)
    await load("documents_cosine", Distance.COSINE, points, recreate=True)
    await load("documents_dot", Distance.DOT, points, recreate=True)
    cos, dot = store("documents_cosine", Distance.COSINE), store("documents_dot", Distance.DOT)
    rows = ["| Запрос | top-5 COSINE | top-5 DOT | Ранжирование совпало |", "|---|---|---|---|"]
    same_all = True
    for q in QUERIES:
        vec = embedder.embed_query(q)
        a, b = await cos.search(vec, 5), await dot.search(vec, 5)
        same = [p.id for p in a] == [p.id for p in b]
        same_all &= same
        rows.append(f"| {q} | {fmt(a)} | {fmt(b)} | {'да' if same else '**нет**'} |")
    # временные коллекции — учебный артефакт: в проде живёт одна боевая
    await cos.client.delete_collection("documents_cosine")
    await dot.client.delete_collection("documents_dot")
    await cos.close()
    await dot.close()
    rows.append(f"\nИтог: ранжирование совпало на **{'всех' if same_all else 'не всех'}** запросах; "
                "временные коллекции documents_cosine / documents_dot удалены.")
    return rows


async def filters(embedder) -> list[str]:
    s = store(get_settings().qdrant_collection)
    out = []

    async def show(title: str, query: str, flt: Filter | None, code: str) -> None:
        vec = embedder.embed_query(query)
        started = time.perf_counter()
        hits = await s.search(vec, 3, flt)
        ms = (time.perf_counter() - started) * 1000
        out.append(f"### {title}\n\nЗапрос: «{query}»\n\n```python\n{code}\n```\n")
        out.append("| # | source | category | status | created_at | score |\n|---|---|---|---|---|---|")
        for i, p in enumerate(hits, 1):
            pl = p.payload
            out.append(f"| {i} | `{pl['source']}` | {pl['category']} | {pl['status']} | {pl['created_at'][:10]} | {p.score:.3f} |")
        out.append(f"\nПоиск: {ms:.1f} мс\n")

    q = "сколько стоит подписка Плюс"
    await show("0. Без фильтра (для сравнения)", q, None, "query_filter=None")
    await show("1. Match по строке: только FAQ приложения", "как выгрузить отчёт",
               Filter(must=[FieldCondition(key="category", match=MatchValue(value="app_faq"))]),
               'Filter(must=[FieldCondition(key="category", match=MatchValue(value="app_faq"))])')
    since = TODAY - timedelta(days=30)
    await show("2. Range по дате: только документы за последние 30 дней", q,
               Filter(must=[FieldCondition(key="created_at", range=DatetimeRange(gte=since.isoformat()))]),
               f'Filter(must=[FieldCondition(key="created_at",\n       range=DatetimeRange(gte="{since.date()}"))])  # today − 30 days')
    await show("3. must + must_not: только FAQ, исключая архивные версии", q,
               Filter(must=[FieldCondition(key="category", match=MatchValue(value="app_faq"))],
                      must_not=[FieldCondition(key="status", match=MatchValue(value="archived"))]),
               'Filter(\n    must=[FieldCondition(key="category", match=MatchValue(value="app_faq"))],\n'
               '    must_not=[FieldCondition(key="status", match=MatchValue(value="archived"))],\n)')
    await s.close()
    return out


async def main() -> None:
    embedder = get_embedding_service()
    md = ["# Результаты экспериментов Qdrant (сгенерировано scripts/qdrant_experiments.py)\n",
          f"Дата эксперимента: {TODAY.date()}. Модель: `{embedder.model}`, точек в коллекции: см. отчёт.\n",
          "## Cosine vs Dot\n", *await cosine_vs_dot(embedder), "\n## Фильтры\n", *await filters(embedder)]
    path = ROOT / "docs" / "vector_store_results.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print(f"→ {path.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
