"""
Б5.3 — тот же RAG без фреймворка: видно, что именно LlamaIndex делает за нас.

  чтение файлов (наш extract_text: MD/PDF/DOCX/HTML) → наивный чанкинг по абзацам →
  эмбеддинги (тот же EmbeddingService) → upsert в свою коллекцию с плоским payload →
  query_points (не deprecated search) → score-guard → промпт system+контекст+вопрос (rag_answer.j2) →
  chat.completions → {answer, top_score, sources} — контракт тот же, что у RAGService.answer().

    python -m app.services.rag_baremetal "Сколько можно вернуть за лечение?"
"""

import sys
import uuid

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.core.config import Settings, get_settings
from app.prompts.loader import render_template
from app.services.documents import load_corpus, naive_chunks
from app.services.embeddings import get_embedding_service
from app.services.rag import REFUSAL

NAMESPACE = uuid.UUID("5f0c4c1e-9b1a-4d2e-8a77-000000005003")
CHUNK_CHARS = 1500  # ≈ 512 токенов для русского текста — сопоставимо с SentenceSplitter(512)


class BareMetalRAG:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.embedder = get_embedding_service()
        self.qdrant = QdrantClient(url=self.s.qdrant_url,
                                   api_key=self.s.qdrant_api_key.get_secret_value() if self.s.qdrant_api_key else None)
        key = (self.s.rag_llm_api_key or self.s.llm.api_key).get_secret_value()
        self.llm = OpenAI(base_url=self.s.rag_llm_base_url or self.s.llm.base_url, api_key=key, timeout=120)
        self.model = self.s.rag_llm_model or self.s.llm.default_model

    def build(self, reindex: bool = False) -> "BareMetalRAG":
        name = self.s.rag_collection_bare
        if self.qdrant.collection_exists(name):
            if not reindex and (self.qdrant.get_collection(name).points_count or 0) > 0:
                return self
            self.qdrant.delete_collection(name)
        self.qdrant.create_collection(name, vectors_config=VectorParams(size=self.s.embedding_dim, distance=Distance.COSINE))
        records = [(d, i, c) for d in load_corpus(self.s.rag_data_dir) for i, c in enumerate(naive_chunks(d.text, CHUNK_CHARS))]
        vectors = self.embedder.embed_documents([c for _, _, c in records])
        self.qdrant.upsert(name, wait=True, points=[
            PointStruct(id=str(uuid.uuid5(NAMESPACE, f"{d.source}#{i}")), vector=v,
                        payload={"text": c, "file_name": d.metadata["file_name"], "source": d.source})
            for (d, i, c), v in zip(records, vectors)
        ])
        return self

    def answer(self, question: str) -> dict:
        hits = self.qdrant.query_points(self.s.rag_collection_bare, query=self.embedder.embed_query(question),
                                        limit=self.s.rag_top_k, with_payload=True).points
        top_score = max((h.score for h in hits), default=0.0)
        sources = [{"text": h.payload["text"][:300], "source": h.payload["file_name"], "score": round(h.score, 3)}
                   for h in hits]
        if top_score < self.s.rag_score_threshold:
            return {"answer": REFUSAL, "top_score": round(top_score, 3), "sources": sources, "llm_called": False}
        prompt = render_template("rag_answer.j2", question=question,
                                 chunks=[{"source": h.payload["file_name"], "text": h.payload["text"]} for h in hits])
        response = self.llm.chat.completions.create(model=self.model, messages=[{"role": "user", "content": prompt}],
                                                    max_tokens=600)
        return {"answer": (response.choices[0].message.content or "").strip(), "top_score": round(top_score, 3),
                "sources": sources, "llm_called": True}


if __name__ == "__main__":
    import json

    rag = BareMetalRAG().build(reindex="--reindex" in sys.argv)
    for q in [a for a in sys.argv[1:] if not a.startswith("--")] or ["Сколько можно вернуть за лечение зубов?"]:
        print(json.dumps(rag.answer(q), ensure_ascii=False, indent=2))
