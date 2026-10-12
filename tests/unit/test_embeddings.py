"""Б5.1 — EmbeddingService: батчинг, кеш (в т. ч. после «рестарта»), нормализация, проверка размерности."""

from types import SimpleNamespace

import numpy as np
import pytest

from app.core.config import LLMSettings, Settings
from app.services.embeddings import EmbeddingService


class FakeEmbeddingsAPI:
    def __init__(self, dim: int = 4):
        self.dim = dim
        self.calls: list[list[str]] = []
        self.embeddings = self

    def create(self, model, input):
        self.calls.append(list(input))
        data = [SimpleNamespace(index=i, embedding=[float(len(t)), 1.0, 2.0, 3.0][: self.dim] + [0.0] * (self.dim - 4))
                for i, t in enumerate(input)]
        return SimpleNamespace(data=data)


def make(tmp_path, api, batch=2, dim=4) -> EmbeddingService:
    settings = Settings(llm=LLMSettings(api_key="t"), embedding_dim=dim, embedding_batch_size=batch,
                        embedding_cache_dir=tmp_path / "cache", embedding_model="test-model")
    return EmbeddingService(settings, client=api)


def test_batching_and_normalization(tmp_path):
    api = FakeEmbeddingsAPI()
    vectors = make(tmp_path, api).embed_texts(["a", "bb", "ccc", "dddd", "e"])
    assert [len(c) for c in api.calls] == [2, 2, 1]  # 5 текстов, батч 2 → 3 запроса
    assert all(abs(np.linalg.norm(v) - 1) < 1e-6 for v in vectors)


def test_cache_survives_restart(tmp_path):
    api = FakeEmbeddingsAPI()
    first = make(tmp_path, api).embed_texts(["тот же текст"])
    restarted = make(tmp_path, api)  # новый экземпляр = «рестарт процесса», кеш на диске
    second = restarted.embed_texts(["тот же текст"])
    assert first == second and len(api.calls) == 1 and restarted.api_calls == 0 and restarted.cache_hits == 1


def test_model_change_does_not_reuse_cache(tmp_path):
    api = FakeEmbeddingsAPI()
    make(tmp_path, api).embed_texts(["текст"])
    other = make(tmp_path, api)
    other.model = "another-model"
    other.embed_texts(["текст"])
    assert len(api.calls) == 2  # другая модель → другой ключ кеша


def test_dimension_mismatch_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="EMBEDDING_DIM"):
        make(tmp_path, FakeEmbeddingsAPI(dim=8), dim=4).embed_texts(["x"])


def test_query_gets_instruction_documents_do_not(tmp_path):
    api = FakeEmbeddingsAPI()
    service = make(tmp_path, api)
    service.embed_query("вычет")
    service.embed_documents(["документ"])
    assert api.calls[0][0].startswith("Instruct:") and api.calls[0][0].endswith("Query: вычет")
    assert api.calls[1] == ["документ"]
