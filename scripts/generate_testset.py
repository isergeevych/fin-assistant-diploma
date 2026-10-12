"""
Б5.6 — черновик golden dataset через RAGAS TestsetGenerator на корпусе data/.
Сырой результат → tests/eval/golden_dataset_raw.csv. Дальше — ОБЯЗАТЕЛЬНАЯ ручная вычитка
(дубли, слишком общие «Что такое X?», нелепые, неверные reference) → tests/eval/golden_dataset.json.

    python -m scripts.generate_testset --size 45
"""

import argparse
from pathlib import Path

from llama_index.core import Document as LIDocument
from openai import AsyncOpenAI
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.run_config import RunConfig
from ragas.testset import TestsetGenerator

from app.core.config import get_settings
from app.services.documents import load_corpus

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=45)
    # RAGAS по умолчанию шлёт 16 параллельных запросов — локальная GPU (Ollama) этого не выдержала (2 BSOD 0x116/0x133)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    s = get_settings()
    base_url = s.rag_llm_base_url or s.llm.base_url
    key = (s.rag_llm_api_key or s.llm.api_key).get_secret_value()
    model = s.rag_llm_model or s.llm.default_model
    client = AsyncOpenAI(base_url=base_url, api_key=key)
    extra = {"extra_body": {"reasoning_effort": s.rag_llm_reasoning_effort}} if s.rag_llm_reasoning_effort else {}
    llm = llm_factory(model, provider="openai", client=client, max_tokens=3000, **extra)
    emb = OpenAIEmbeddings(client=AsyncOpenAI(base_url=s.embedding_base_url, api_key=s.embedding_api_key.get_secret_value()),
                           model=s.embedding_model)

    docs = [LIDocument(text=d.text, metadata={"source": d.source, "file_name": d.metadata["file_name"]})
            for d in load_corpus(s.data_dir) if d.metadata["category"] != "misc"]
    generator = TestsetGenerator(llm=llm, embedding_model=emb)
    testset = generator.generate_with_llamaindex_docs(docs, testset_size=args.size,
                                                      run_config=RunConfig(max_workers=args.workers, timeout=600))
    df = testset.to_pandas()
    out = ROOT / "tests" / "eval" / "golden_dataset_raw.csv"
    df.to_csv(out, index=False, encoding="utf-8")
    print(f"сгенерировано {len(df)} пар → {out.relative_to(ROOT)}; колонки: {list(df.columns)}")


if __name__ == "__main__":
    main()
