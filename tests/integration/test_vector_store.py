"""
Б5.2 — smoke-тест VectorStore против настоящего Qdrant (docker compose up -d qdrant).
Пропускается, если Qdrant недоступен — unit-набор остаётся без внешних зависимостей.
"""

import uuid

import pytest
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from app.core.config import get_settings
from app.services.vector_store import DimensionMismatchError, VectorStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def store():
    s = get_settings()
    vs = VectorStore(s.qdrant_url, s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None,
                     f"test_smoke_{uuid.uuid4().hex[:8]}", 4)
    try:
        await vs.client.get_collections()
    except Exception:  # noqa: BLE001
        pytest.skip("Qdrant недоступен")
    yield vs
    await vs.client.delete_collection(vs.collection)
    await vs.close()


async def test_ensure_upsert_search_filter(store):
    await store.ensure_collection()
    await store.ensure_collection()  # повторный вызов безопасен
    points = [
        PointStruct(id=str(uuid.uuid4()), vector=[1, 0, 0, 0], payload={"category": "taxes", "status": "active"}),
        PointStruct(id=str(uuid.uuid4()), vector=[0.9, 0.1, 0, 0], payload={"category": "taxes", "status": "archived"}),
        PointStruct(id=str(uuid.uuid4()), vector=[0, 1, 0, 0], payload={"category": "app_faq", "status": "active"}),
    ]
    await store.upsert(points)
    await store.upsert(points)  # идемпотентно по id
    assert await store.count() == 3

    hits = await store.search([1, 0, 0, 0], top_k=2)
    assert hits[0].payload["category"] == "taxes"
    active = await store.search([1, 0, 0, 0], top_k=3, query_filter=Filter(
        must_not=[FieldCondition(key="status", match=MatchValue(value="archived"))]))
    assert all(h.payload["status"] == "active" for h in active)


async def test_wrong_dimension_is_rejected(store):
    await store.ensure_collection()
    with pytest.raises(DimensionMismatchError):
        await store.upsert([PointStruct(id=str(uuid.uuid4()), vector=[1, 0, 0], payload={})])
