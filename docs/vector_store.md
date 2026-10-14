# Векторная БД: Qdrant (Б5.2)

## Как поднято

- Сервис `qdrant` в `docker-compose.yml`: образ `qdrant/qdrant:v1.19.2-unprivileged` (процесс не под root),
  порты `6333` (REST + dashboard) и `6334` (gRPC) только на `127.0.0.1`, named volume `qdrant_storage`,
  healthcheck по TCP, `restart: unless-stopped`. `app` стартует после `qdrant: service_healthy`.
- **API-ключ даже на dev** (`QDRANT__SERVICE__API_KEY` из `.env`): без ключа — 401, с ключом — 200 (проверено).
- Конфиг — `.env`: `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION=documents`, `EMBEDDING_DIM=1024`.
  В коде нет хардкода `localhost:6333`.

Dashboard: http://localhost:6333/dashboard (ввести API-ключ).

## Загрузка: `scripts/load_to_qdrant.py`

Корпус — `data/` (56 документов: MD, PDF, DOCX, HTML; см. `docs/data_inventory.md`), чанки по ~300 символов →
**106 точек**. Payload: `source`, `text`, `created_at`, `category` (доменное поле), `status` (active/archived),
`version`, `file_type`, `chunk_index`. Payload-индексы: `source` (KEYWORD), `created_at` (DATETIME),
`category` (KEYWORD), `status` (KEYWORD).

| Проверка | Результат |
|---|---|
| Первый прогон | `points_count = 106` |
| Повторный прогон | `points_count = 106` — дублей нет: id = `uuid5(NAMESPACE, source#chunk_index)` |
| Размерность коллекции | `vectors.size = 1024` = `EMBEDDING_DIM` |
| Попытка с другой размерностью (768) | `DimensionMismatchError: Коллекция 'documents' создана с размерностью 1024, а EMBEDDING_DIM=768…` |

**Тонкость, найденная по ходу:** идемпотентность держится на id = (документ, номер чанка). Если поменять
нарезку, старые точки с прежними id останутся «сиротами» (было 82 точки при чанке 450 символов, стало бы
82 + новые). Поэтому смена чанкинга или модели — только через `--recreate` (полная переиндексация).

## `app/services/vector_store.py`

Тонкая обёртка над `AsyncQdrantClient` — остальной код не знает про qdrant-client:
`ensure_collection()` (коллекция + payload-индексы + проверка размерности), `upsert(points, batch_size=256)`
(`wait=True` на последнем батче), `search(query_vector, top_k, query_filter)` через `query_points`
(не deprecated `search`) → `list[ScoredPoint]`. Клиент создаётся один раз.

**HNSW:** заданы явно `m=16, ef_construct=100` — это default Qdrant. На корпусе в сотни–тысячи чанков граф
строится мгновенно, а приближённый поиск почти не отличается от точного; поднимать `m` / `ef_construct` имеет
смысл от сотен тысяч векторов.

## Метрика: cosine vs dot

Те же 106 векторов залиты в `documents_cosine` и `documents_dot`, на 5 вопросах из домена сравнены top-5 id.
Полная таблица (id, score) — [`vector_store_results.md`](vector_store_results.md).

| Запрос | top-5 совпали |
|---|---|
| можно ли вернуть деньги за лечение зубов | да |
| мне звонят из банка и просят код из смс | да |
| сколько стоит подписка Плюс в приложении | да |
| что будет с вкладом если у банка отзовут лицензию | да |
| как погасить кредит раньше срока | да |

Ранжирование совпало на всех 5 — sanity check пройден: векторы нормализованы (`EmbeddingService` делает
L2-нормализацию), а для единичных векторов cosine = dot product. **В production оставляю COSINE**: он
устойчив, если когда-нибудь в коллекцию попадёт ненормализованный вектор (например, при смене провайдера
эмбеддингов), а разницы в скорости на нашем объёме нет. Временные коллекции после эксперимента удалены.

## Фильтры по metadata

Зачем это нужно, видно на первом же примере: **без фильтра** на вопрос «сколько стоит подписка Плюс»
первым находится **архивный** тариф v1 (249 ₽, отменён в марте 2026) — ассистент назвал бы устаревшую цену.

| # | Фильтр | Запрос | top-1 | Что показывает |
|---|---|---|---|---|
| 0 | нет | сколько стоит подписка Плюс | `app_faq/tariffs_v1.md` (archived, 0.642) | ❌ устаревший документ выше актуального |
| 1 | match `category = app_faq` | как выгрузить отчёт | `app_faq/export_reports.md` (0.701) | поиск только в FAQ приложения |
| 2 | `created_at ≥ today − 30 days` | сколько стоит подписка Плюс | `app_faq/tariffs_v2.pdf` (0.579) | свежий документ вместо старого |
| 3 | must `category = app_faq` + must_not `status = archived` | сколько стоит подписка Плюс | `app_faq/tariffs_v2.pdf` (0.579) | самый частый production-паттерн: «тенант, но без архива» |

Код фильтров и top-3 по каждому — в [`vector_store_results.md`](vector_store_results.md). Время поиска с
фильтром — ~3 мс (payload-индексы работают).

**Вывод для RAG (Б5.5):** в боевом запросе по умолчанию исключаем `status = archived`, иначе устаревшие
версии документов будут конкурировать с актуальными на равных.

## pgvector (опционально)

Не делали: Qdrant уже нужен диплому (фильтры с индексами, в планах hybrid search), а время ограничено.
Сравнение с pgvector — в списке возможных улучшений.
