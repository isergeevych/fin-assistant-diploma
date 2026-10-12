"""
Б5.1 — сервис эмбеддингов.

    from app.services.embeddings import embed_texts
    vectors = embed_texts(["текст 1", "текст 2"])   # list[list[float]], L2-нормализованные

Внутри:
  - OpenAI-совместимый /v1/embeddings (Ollama локально / OpenRouter в проде) — провайдер
    меняется через .env (EMBEDDING_BASE_URL, EMBEDDING_MODEL), код не трогается;
  - батчинг по EMBEDDING_BATCH_SIZE (64): один HTTP-запрос на пачку, а не на текст;
  - сетевые проблемы: таймаут 60 с + ретраи SDK (экспонента на 429/5xx/обрывы соединения);
  - нормализация: векторы приводятся к длине 1, поэтому cosine == dot product (Б5.2);
  - дисковый кеш (diskcache) с ключом model + sha256(text): повтор того же текста — без
    запроса к API, в том числе после рестарта. Модель в ключе — при смене модели старые
    векторы другой размерности не подмешаются.

Выбор модели и его обоснование — docs/embeddings.md.
"""

import hashlib
import sys
import time
from functools import lru_cache

import diskcache
import numpy as np
import structlog
from openai import OpenAI

from app.core.config import Settings, get_settings

log = structlog.get_logger("embeddings")


class EmbeddingService:
    def __init__(self, settings: Settings, client: OpenAI | None = None):
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim
        self.query_instruction = settings.embedding_query_instruction
        self.batch_size = settings.embedding_batch_size
        self.client = client or OpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key.get_secret_value(),
            timeout=60,
            max_retries=3,
        )
        self.cache = diskcache.Cache(str(settings.embedding_cache_dir))
        self.api_calls = 0
        self.cache_hits = 0

    def _key(self, text: str) -> str:
        return f"{self.model}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for i, text in enumerate(texts):
            cached = self.cache.get(self._key(text))
            if cached is not None:
                result[i] = cached
                self.cache_hits += 1
            else:
                missing.append(i)

        for start in range(0, len(missing), self.batch_size):
            batch_idx = missing[start : start + self.batch_size]
            batch = [texts[i] for i in batch_idx]
            started = time.perf_counter()
            response = self.client.embeddings.create(model=self.model, input=batch)
            self.api_calls += 1
            vectors = [normalize(d.embedding) for d in sorted(response.data, key=lambda d: d.index)]
            if vectors and len(vectors[0]) != self.dim:
                raise ValueError(
                    f"Модель {self.model} вернула размерность {len(vectors[0])}, а EMBEDDING_DIM={self.dim}: "
                    "поправьте .env — иначе в векторной БД окажутся несовместимые векторы"
                )
            for i, vec in zip(batch_idx, vectors):
                result[i] = vec
                self.cache.set(self._key(texts[i]), vec)
            log.info("embeddings.batch", model=self.model, size=len(batch),
                     duration_ms=round((time.perf_counter() - started) * 1000))
        return result  # type: ignore[return-value]

    def embed_query(self, text: str) -> list[float]:
        """Запрос — с инструкцией (как query: у E5): модель понимает, что это вопрос, а не документ."""
        return self.embed_texts([self.query_instruction + text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Документы — без префикса: так их рекомендуют кодировать авторы qwen3-embedding."""
        return self.embed_texts(texts)


def normalize(vector: list[float]) -> list[float]:
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    return (arr / norm).tolist() if norm > 0 else arr.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    return float(np.dot(np.asarray(a), np.asarray(b)))  # векторы уже нормализованы


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService(get_settings())


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embedding_service().embed_texts(texts)


if __name__ == "__main__":
    # Проверка кеша: запустите дважды — второй раз api_calls=0 и время заметно меньше
    service = get_embedding_service()
    sample = sys.argv[1:] or ["Как получить налоговый вычет за лечение?"]
    started = time.perf_counter()
    vectors = service.embed_texts(sample)
    print(f"model={service.model} dim={len(vectors[0])} texts={len(sample)} "
          f"api_calls={service.api_calls} cache_hits={service.cache_hits} "
          f"time={(time.perf_counter() - started) * 1000:.1f} ms")
