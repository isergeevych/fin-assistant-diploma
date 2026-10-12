"""
Б5.6 — прогон golden dataset через текущий RAG и метрики RAGAS 0.4 + has_citation.

    python -m scripts.run_eval --label baseline
    python -m scripts.run_eval --label semantic --chunk-strategy semantic     # A/B 1: чанкинг (своя коллекция)
    python -m scripts.run_eval --label no_rerank --no-rerank                  # A/B 2: re-ranker

Правила A/B: один и тот же golden, меняется ОДИН параметр, артефакт с timestamp —
tests/eval/results/{YYYY-MM-DD_HHMM}_{label}.csv (по строкам) + .json (агрегаты и конфигурация).
"""

import argparse
import asyncio
import json
import statistics
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.core.config import get_settings
from app.eval.metrics import build_embeddings, build_judge, build_metrics, eval_row
from app.services.rag import RAGService

ROOT = Path(__file__).resolve().parents[1]
METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "has_citation"]


def configured_settings(args):
    s = get_settings()
    update = {}
    if args.chunk_strategy and args.chunk_strategy != s.rag_chunk_strategy:
        update["rag_chunk_strategy"] = args.chunk_strategy
        update["rag_collection"] = f"kb_{args.chunk_strategy}"
        update["docstore_dir"] = s.docstore_dir.parent / f"docstore_{args.chunk_strategy}"
    if args.chunk_size:
        update["rag_chunk_size"] = args.chunk_size
        update["rag_collection"] = f"kb_size_{args.chunk_size}"
        update["docstore_dir"] = s.docstore_dir.parent / f"docstore_size_{args.chunk_size}"
    if args.no_rerank:
        update["rag_rerank_enabled"] = False
    if args.top_k:
        update["rag_retrieval_top_k"] = args.top_k
    return s.model_copy(update=update) if update else s


async def main_async(args) -> None:
    settings = configured_settings(args)
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    items = golden["items"][: args.limit] if args.limit else golden["items"]
    rag = RAGService(settings)
    if settings.rag_collection != get_settings().rag_collection:  # A/B-вариант индексации — своя коллекция
        from app.services.ingestion import run_ingestion

        await asyncio.to_thread(run_ingestion, [settings.data_dir], settings)
    await asyncio.to_thread(rag.build)
    judge, judge_model = build_judge(settings)
    metrics = build_metrics(judge, build_embeddings(settings))
    sem = asyncio.Semaphore(args.concurrency)

    async def one(item: dict) -> dict:
        async with sem:
            out = await asyncio.to_thread(rag.evaluate_inputs, item["user_input"])
            scores = await eval_row(metrics, item["user_input"], out["answer"], out["retrieved_contexts"], item["reference"])
        print(f"  {item['id']:<6} faith={scores.get('faithfulness')} rel={scores.get('answer_relevancy')} "
              f"prec={scores.get('context_precision')} rec={scores.get('context_recall')} cite={scores.get('has_citation')}")
        return {"id": item["id"], "type": item.get("type"), "user_input": item["user_input"], "reference": item["reference"],
                "response": out["answer"], "sources": "; ".join(out["sources"]),
                "retrieved_contexts": json.dumps(out["retrieved_contexts"], ensure_ascii=False),
                "top_score": out["top_score"], "latency_ms": out["latency_ms"], **scores}

    rows = await asyncio.gather(*[one(i) for i in items])
    df = pd.DataFrame(rows)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = ROOT / "tests" / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{stamp}_{args.label}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8")
    agg = {m: round(float(df[m].dropna().mean()), 3) if df[m].notna().any() else None for m in METRICS}
    summary = {
        "label": args.label, "timestamp": stamp, "items": len(df), "golden_version": golden.get("version"),
        "metrics": agg, "metric_errors": {m: int(df[m].isna().sum()) for m in METRICS},
        "latency_ms_avg": round(statistics.mean(df["latency_ms"]), 0),
        "config": {"llm": settings.rag_llm_model or settings.llm.default_model, "judge": judge_model,
                   "embeddings": settings.embedding_model, "chunk_strategy": settings.rag_chunk_strategy,
                   "chunk_size": settings.rag_chunk_size, "chunk_overlap": settings.rag_chunk_overlap,
                   "retrieval_top_k": settings.rag_retrieval_top_k, "rerank": settings.rag_rerank_enabled,
                   "rerank_top_n": settings.rag_rerank_top_n, "collection": settings.rag_collection},
    }
    csv_path.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["metrics"], ensure_ascii=False), f"latency={summary['latency_ms_avg']} мс")
    print(f"→ {csv_path.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default=str(ROOT / "tests" / "eval" / "golden_dataset.json"))
    parser.add_argument("--label", required=True)
    parser.add_argument("--chunk-strategy", choices=["fixed", "recursive", "semantic"])
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=1)  # локальная GPU: по одному (BSOD при параллели, GUIDE #35)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
