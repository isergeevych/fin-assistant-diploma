"""
Б5.4 — эксперимент: 3 стратегии chunking × метрики retrieval, re-ranker, подбор параметров.
Сырые числа → tests/eval/results/chunking_experiment.json; таблицы и выводы — docs/chunking_experiment.md.

    python -m scripts.chunking_experiment                # всё
    python -m scripts.chunking_experiment --no-rerank    # без cross-encoder (нет torch)
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from llama_index.core import Document as LIDocument
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from app.core.config import get_settings
from app.services.chunking import build_splitter
from app.services.documents import load_corpus
from app.services.embeddings import get_embedding_service
from app.services.rag import ServiceEmbedding
from app.services.retrieval_eval import evaluate

ROOT = Path(__file__).resolve().parents[1]
DATASET = json.loads((ROOT / "tests/eval/retrieval_dataset.json").read_text(encoding="utf-8"))["items"]


def li_documents() -> list[LIDocument]:
    docs = []
    for d in load_corpus(get_settings().data_dir):
        doc = LIDocument(text=d.text, metadata={"source": d.source})
        doc.excluded_embed_metadata_keys = ["source"]  # эмбеддинг — только по тексту
        doc.excluded_llm_metadata_keys = ["source"]
        docs.append(doc)
    return docs


def build_index(strategy: str, chunk_size: int, overlap: int, embed, client: QdrantClient, collection: str):
    nodes = build_splitter(strategy, embed, chunk_size, overlap).get_nodes_from_documents(li_documents())
    if client.collection_exists(collection):
        client.delete_collection(collection)
    store = QdrantVectorStore(client=client, collection_name=collection)
    index = VectorStoreIndex(nodes, storage_context=StorageContext.from_defaults(vector_store=store), embed_model=embed)
    per_doc = {}
    for n in nodes:
        per_doc[n.metadata["source"]] = per_doc.get(n.metadata["source"], 0) + 1
    stats = {"chunks_total": len(nodes), "chunks_per_doc": round(statistics.mean(per_doc.values()), 2),
             "avg_chunk_chars": round(statistics.mean(len(n.get_content()) for n in nodes))}
    return index, stats


def run(index, top_k: int, reranker=None, rerank_pool: int = 20) -> dict:
    retriever = index.as_retriever(similarity_top_k=rerank_pool if reranker else top_k)
    latencies = []

    def retrieve(q: str) -> list[str]:
        started = time.perf_counter()
        nodes = retriever.retrieve(q)
        if reranker:
            cands = [{"text": n.node.get_content(), "source": n.node.metadata["source"]} for n in nodes]
            ranked = reranker(q, cands, top_n=top_k)
            sources = [c["source"] for c in ranked]
        else:
            sources = [n.node.metadata["source"] for n in nodes]
        latencies.append((time.perf_counter() - started) * 1000)
        return sources

    retrieve(DATASET[0]["question"])  # прогрев: загрузка моделей не должна попасть в замер
    latencies.clear()
    rep = evaluate(DATASET, retrieve)
    return {"hit_rate@5": rep.hit_rate_at_5, "mrr@10": rep.mrr_at_10, "recall@10": rep.recall_at_10,
            "latency_ms": round(statistics.mean(latencies), 1), "per_question": rep.per_question}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-rerank", action="store_true")
    args = parser.parse_args()
    s = get_settings()
    client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None)
    embed = ServiceEmbedding(get_embedding_service())
    results = {"strategies": {}, "rerank": {}, "grid": []}

    indexes = {}
    for strategy in ("fixed", "recursive", "semantic"):
        index, stats = build_index(strategy, 512, 64, embed, client, f"docs_{strategy}")
        indexes[strategy] = index
        metrics = run(index, top_k=10)
        results["strategies"][strategy] = {**stats, **metrics}
        print(f"{strategy:<10} chunks={stats['chunks_total']:<4} per_doc={stats['chunks_per_doc']:<5} "
              f"avg_chars={stats['avg_chunk_chars']:<5} HR@5={metrics['hit_rate@5']} MRR@10={metrics['mrr@10']} "
              f"R@10={metrics['recall@10']} {metrics['latency_ms']} мс")

    best = max(results["strategies"], key=lambda k: (results["strategies"][k]["mrr@10"], results["strategies"][k]["recall@10"]))
    results["best_strategy"] = best
    print(f"лучшая стратегия: {best}")

    if not args.no_rerank:
        from app.services.reranker import rerank

        with_rr = run(indexes[best], top_k=10, reranker=rerank, rerank_pool=20)
        results["rerank"] = {"without": {k: v for k, v in results["strategies"][best].items() if k != "per_question"},
                             "with": with_rr}
        print(f"{best}+rerank   HR@5={with_rr['hit_rate@5']} MRR@10={with_rr['mrr@10']} R@10={with_rr['recall@10']} "
              f"{with_rr['latency_ms']} мс")

    grid_strategy = best if best != "semantic" else "recursive"  # у semantic нет chunk_size/overlap
    for size in (256, 512):
        for overlap in (32, 64):
            index, stats = build_index(grid_strategy, size, overlap, embed, client, f"docs_grid_{size}_{overlap}")
            for k in (10, 20):
                m = run(index, top_k=k)
                results["grid"].append({"strategy": grid_strategy, "chunk_size": size, "overlap": overlap, "top_k": k,
                                        **stats, **{x: m[x] for x in ("hit_rate@5", "mrr@10", "recall@10", "latency_ms")}})
                print(f"grid {grid_strategy} size={size} overlap={overlap} k={k}: chunks={stats['chunks_total']} "
                      f"HR@5={m['hit_rate@5']} MRR@10={m['mrr@10']} R@10={m['recall@10']}")
            client.delete_collection(f"docs_grid_{size}_{overlap}")

    out = ROOT / "tests/eval/results/chunking_experiment.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
