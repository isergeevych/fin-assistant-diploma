"""
Б5.1 — выбор embedding-модели на mini-benchmark из предметной области.

Для каждой модели: сколько троек (query, relevant, irrelevant) ранжировано верно
(cos(q, rel) > cos(q, irr)), средний зазор cos(q, rel) − cos(q, irr), размерность, время.
Все модели вызываются через один OpenAI-совместимый /v1/embeddings (Ollama), чтобы
сравнение было честным. Облачные модели (OpenRouter) добавляются флагом --cloud при наличии ключа.

    python -m scripts.embedding_benchmark            # локальные модели
    python -m scripts.embedding_benchmark --cloud    # + openai/text-embedding-3-small, baai/bge-m3 через OpenRouter
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np
import tiktoken
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
BENCH = json.loads((ROOT / "tests" / "eval" / "mini_benchmark.json").read_text(encoding="utf-8"))

LOCAL = [  # (имя, base_url, model, $ за 1M токенов при облачном аналоге)
    ("bge-m3 (Ollama)", "http://localhost:11434/v1", "bge-m3"),
    ("qwen3-embedding 0.6B (Ollama)", "http://localhost:11434/v1", "qwen3-embedding:0.6b"),
    ("nomic-embed-text (Ollama, EN)", "http://localhost:11434/v1", "nomic-embed-text"),
]
CLOUD = [
    ("text-embedding-3-small (OpenRouter)", "https://openrouter.ai/api/v1", "openai/text-embedding-3-small"),
    ("bge-m3 (OpenRouter)", "https://openrouter.ai/api/v1", "baai/bge-m3"),
]
PRICES_PER_1M = {"openai/text-embedding-3-small": 0.02, "baai/bge-m3": 0.01, "openai/text-embedding-3-large": 0.13}


def embed(client: OpenAI, model: str, texts: list[str]) -> np.ndarray:
    data = client.embeddings.create(model=model, input=texts).data
    arr = np.asarray([d.embedding for d in sorted(data, key=lambda d: d.index)], dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def evaluate(name: str, base_url: str, model: str, api_key: str) -> dict:
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=120)
    queries = [b["query"] for b in BENCH]
    rel = [b["relevant"] for b in BENCH]
    irr = [b["irrelevant"] for b in BENCH]
    embed(client, model, ["прогрев"])  # загрузка модели в память не должна попасть в замер
    started = time.perf_counter()
    q, r, i = embed(client, model, queries), embed(client, model, rel), embed(client, model, irr)
    elapsed_ms = (time.perf_counter() - started) * 1000
    s_rel, s_irr = (q * r).sum(axis=1), (q * i).sum(axis=1)
    return {
        "name": name, "model": model, "dim": int(q.shape[1]),
        "correct": int((s_rel > s_irr).sum()), "total": len(BENCH),
        "mean_margin": round(float(np.mean(s_rel - s_irr)), 4),
        "min_margin": round(float(np.min(s_rel - s_irr)), 4),
        "mean_rel": round(float(np.mean(s_rel)), 4),
        "ms_per_text": round(elapsed_ms / (3 * len(BENCH)), 1),
        "per_pair": [round(float(x), 3) for x in (s_rel - s_irr)],
    }


def indexing_cost(n_docs: int = 60, avg_tokens: int = 700) -> list[str]:
    """Стоимость индексации корпуса: N документов × средняя длина (токены cl100k)."""
    total = n_docs * avg_tokens
    return [f"| {m} | {total:,} | ${total * p / 1_000_000:.4f} |".replace(",", " ") for m, p in PRICES_PER_1M.items()]


def main() -> None:
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--cloud", action="store_true")
    args = parser.parse_args()
    runs = [(n, u, m, "ollama") for n, u, m in LOCAL]
    if args.cloud:
        key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("LLM__API_KEY", "")
        runs += [(n, u, m, key) for n, u, m in CLOUD]

    results = []
    for name, url, model, key in runs:
        try:
            res = evaluate(name, url, model, key)
        except Exception as e:  # noqa: BLE001 — отчёт должен собраться, даже если одна модель недоступна
            print(f"{name}: недоступна — {type(e).__name__}: {e}")
            continue
        results.append(res)
        print(f"{name:<38} dim={res['dim']:<5} верно {res['correct']}/{res['total']}  "
              f"зазор ср={res['mean_margin']:+.3f} мин={res['min_margin']:+.3f}  {res['ms_per_text']} мс/текст")

    enc = tiktoken.get_encoding("cl100k_base")
    sample_tokens = statistics.mean(len(enc.encode(b["relevant"])) for b in BENCH)
    out = ROOT / "tests" / "eval" / "embedding_benchmark_results.json"
    out.write_text(json.dumps({"results": results, "avg_chunk_tokens_sample": sample_tokens,
                               "indexing_cost_rows": indexing_cost()}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
