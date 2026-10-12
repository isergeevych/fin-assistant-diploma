"""
Б5.2 — загрузка корпуса data/ в Qdrant. Идемпотентно: повторный запуск не плодит дубли.

  1. ensure_collection: создаёт коллекцию (COSINE, размерность EMBEDDING_DIM) и payload-индексы;
     если коллекция есть с другой размерностью — падает с понятной ошибкой;
  2. читает документы всех форматов, режет на чанки;
  3. id точки = uuid5(NAMESPACE, source + chunk_index) — ключ к идемпотентности: та же точка
     перезаписывается (upsert), а не добавляется второй раз;
  4. upsert батчами по 256, wait=True на последнем.

    python -m scripts.load_to_qdrant [--collection documents] [--distance cosine|dot]
"""

import argparse
import asyncio
import uuid

from qdrant_client.models import Distance, PointStruct
from tqdm import tqdm

from app.core.config import get_settings
from app.services.documents import load_corpus, naive_chunks
from app.services.embeddings import get_embedding_service
from app.services.vector_store import VectorStore

NAMESPACE = uuid.UUID("5f0c4c1e-9b1a-4d2e-8a77-000000005002")
CHUNK_CHARS = 300


def build_points(embedder) -> list[PointStruct]:
    settings = get_settings()
    records = []
    for doc in load_corpus(settings.data_dir):
        for idx, chunk in enumerate(naive_chunks(doc.text, CHUNK_CHARS)):
            records.append((doc, idx, chunk))
    texts = [r[2] for r in records]
    vectors = []
    for i in tqdm(range(0, len(texts), 64), desc="эмбеддинги", unit="батч"):
        vectors += embedder.embed_documents(texts[i : i + 64])
    return [
        PointStruct(
            id=str(uuid.uuid5(NAMESPACE, f"{doc.source}#{idx}")),
            vector=vec,
            payload={**doc.metadata, "chunk_index": idx, "text": chunk},
        )
        for (doc, idx, chunk), vec in zip(records, vectors)
    ]


async def load(collection: str, distance: Distance, points: list[PointStruct] | None = None,
               recreate: bool = False) -> int:
    settings = get_settings()
    store = VectorStore(settings.qdrant_url,
                        settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
                        collection, settings.embedding_dim, distance)
    if recreate and await store.client.collection_exists(collection):
        # Идемпотентность держится на id = (source, chunk_index). При смене нарезки или модели старые
        # точки с другими id остались бы «сиротами» — поэтому переиндексация идёт с чистой коллекции.
        await store.client.delete_collection(collection)
    await store.ensure_collection()
    points = points if points is not None else build_points(get_embedding_service())
    await store.upsert(points)
    count = await store.count()
    await store.close()
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default=None)
    parser.add_argument("--distance", choices=["cosine", "dot"], default="cosine")
    parser.add_argument("--recreate", action="store_true", help="удалить коллекцию и проиндексировать заново")
    args = parser.parse_args()
    collection = args.collection or get_settings().qdrant_collection
    distance = Distance.COSINE if args.distance == "cosine" else Distance.DOT
    count = asyncio.run(load(collection, distance, recreate=args.recreate))
    print(f"Коллекция {collection!r} ({args.distance}): points_count = {count}")


if __name__ == "__main__":
    main()
