"""
Б5.4 — re-ranker: cross-encoder BAAI/bge-reranker-v2-m3 (мультиязычный, open-source).

Bi-encoder (эмбеддинги) кодирует вопрос и документ ПО ОТДЕЛЬНОСТИ — быстро, но грубо.
Cross-encoder читает пару «вопрос + фрагмент» ЦЕЛИКОМ и оценивает релевантность точнее,
но медленно, поэтому применяется только к короткому списку кандидатов (top-20 → top-N).

Зависимость тяжёлая (torch) — вынесена в optional-группу `rerank` и не попадает в Docker-образ сервиса.
Модель скачивается в HF_HOME (на этой машине — D:\\hf-cache, на C: нет места).
"""

from functools import lru_cache

MODEL_NAME = "BAAI/bge-reranker-v2-m3"


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(MODEL_NAME, max_length=512)


def rerank(query: str, candidates: list[dict], top_n: int = 5, text_key: str = "text") -> list[dict]:
    """candidates — словари с текстом; возвращает top_n по убыванию rerank_score."""
    if not candidates:
        return []
    scores = _model().predict([(query, c[text_key]) for c in candidates], batch_size=16)
    ranked = sorted(zip(candidates, scores), key=lambda x: float(x[1]), reverse=True)
    return [{**c, "rerank_score": round(float(s), 4)} for c, s in ranked[:top_n]]
