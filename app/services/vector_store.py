"""
Б5.2 — тонкая обёртка над AsyncQdrantClient: остальной код не знает про qdrant-client напрямую
(в Б5.3 здесь появится LlamaIndex — интерфейс не меняется).

Клиент создаётся один раз (lifespan / CLI) и переиспользуется; на каждый вызов новый не создаётся.

HNSW: оставляю default Qdrant (m=16, ef_construct=100) — на корпусе в сотни–тысячи чанков граф
строится мгновенно, а полный (точный) перебор здесь почти не медленнее; поднимать m/ef_construct
имеет смысл от сотен тысяч векторов, где растёт цена ошибки приближённого поиска. Задано явно,
чтобы значение было видно и менялось осознанно.
"""

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    Filter,
    HnswConfigDiff,
    PayloadSchemaType,
    PointStruct,
    ScoredPoint,
    VectorParams,
)

PAYLOAD_INDEXES = {
    "source": PayloadSchemaType.KEYWORD,
    "created_at": PayloadSchemaType.DATETIME,
    "category": PayloadSchemaType.KEYWORD,  # доменное поле: налоги / вклады / безопасность / FAQ
    "status": PayloadSchemaType.KEYWORD,    # active / archived
}
HNSW = HnswConfigDiff(m=16, ef_construct=100)


class DimensionMismatchError(RuntimeError):
    pass


class VectorStore:
    def __init__(self, url: str, api_key: str | None, collection: str, dim: int,
                 distance: Distance = Distance.COSINE, client: AsyncQdrantClient | None = None):
        self.client = client or AsyncQdrantClient(url=url, api_key=api_key)
        self.collection = collection
        self.dim = dim
        self.distance = distance

    async def ensure_collection(self) -> None:
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.dim, distance=self.distance),
                hnsw_config=HNSW,
            )
        else:
            info = await self.client.get_collection(self.collection)
            existing = info.config.params.vectors.size
            if existing != self.dim:
                raise DimensionMismatchError(
                    f"Коллекция {self.collection!r} создана с размерностью {existing}, а EMBEDDING_DIM={self.dim}. "
                    "Смена модели эмбеддингов требует новой коллекции и переиндексации — заливать нельзя."
                )
        for field, schema in PAYLOAD_INDEXES.items():
            await self.client.create_payload_index(self.collection, field_name=field, field_schema=schema)

    async def upsert(self, points: list[PointStruct], batch_size: int = 256) -> None:
        for p in points:
            if len(p.vector) != self.dim:
                raise DimensionMismatchError(f"точка {p.id}: размерность {len(p.vector)} ≠ {self.dim}")
        for i in range(0, len(points), batch_size):
            await self.client.upsert(
                collection_name=self.collection,
                points=points[i : i + batch_size],
                wait=(i + batch_size >= len(points)),  # на последнем батче ждём индексации — сразу можно искать
            )

    async def search(self, query_vector: list[float], top_k: int = 5,
                     query_filter: Filter | None = None) -> list[ScoredPoint]:
        result = await self.client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )
        return result.points

    async def count(self) -> int:
        return (await self.client.get_collection(self.collection)).points_count or 0

    async def close(self) -> None:
        await self.client.close()
