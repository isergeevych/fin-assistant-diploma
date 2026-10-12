"""
Б5.5 — ingestion базы знаний: файлы → обогащённые документы → IngestionPipeline → Qdrant.

Документы:
  - PDF разбивается ПОСТРАНИЧНО (doc_id = source#pN, metadata.page) — в цитате можно указать страницу;
  - метаданные: source (путь), file_name, category (папка data/<category>/…), last_modified (mtime),
    author (DOCX core properties), version (из имени *_v2), status (archived, если есть версия новее);
  - служебные поля исключены из эмбеддинга (excluded_embed_metadata_keys), в контекст LLM идёт только
    file_name и page — чтобы не зашумлять ни вектор, ни промпт (находка Б5.3).

Пайплайн: SentenceSplitter(512/64, русские предложения) → эмбеддинги → QdrantVectorStore,
docstore = SimpleDocumentStore (на диске var/docstore) + DocstoreStrategy.UPSERTS:
повторный запуск по хешам документов пропускает неизменённые, изменённые — переиндексирует.

Файлы, которые не удалось прочитать, переименовываются в <имя>.failed и пишутся в лог (не валят всю загрузку).

    python -m app.services.ingestion data/         # или scripts/ingest.py
"""

import io
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog
from llama_index.core import Document as LIDocument
from llama_index.core.ingestion import DocstoreStrategy, IngestionPipeline
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import AsyncQdrantClient, QdrantClient

from app.core.config import Settings, get_settings
from app.services.chunking import build_splitter
from app.services.documents import SUPPORTED, VERSION_RE, extract_text

log = structlog.get_logger("ingestion")
SKIP_DIRS = {"finance", "rag-block-03"}
EMBED_EXCLUDED = ["source", "category", "last_modified", "author", "version", "status", "file_type", "page", "file_name"]
LLM_EXCLUDED = ["source", "category", "last_modified", "author", "version", "status", "file_type"]


@dataclass
class IngestReport:
    files_total: int = 0
    documents: int = 0
    changed: int = 0
    unchanged: int = 0
    nodes_written: int = 0
    failed: list[str] = field(default_factory=list)


def _versions(files: list[Path]) -> dict[str, int]:
    latest: dict[str, int] = {}
    for p in files:
        if m := VERSION_RE.search(p.stem):
            base = str(p.parent / VERSION_RE.sub("", p.stem))
            latest[base] = max(latest.get(base, 0), int(m.group(1)))
    return latest


def _author(path: Path) -> str | None:
    if path.suffix.lower() != ".docx":
        return None
    import docx

    return docx.Document(io.BytesIO(path.read_bytes())).core_properties.author or None


def file_to_documents(path: Path, data_dir: Path, latest_versions: dict[str, int]) -> list[LIDocument]:
    rel = path.relative_to(data_dir).as_posix()
    m = VERSION_RE.search(path.stem)
    version = int(m.group(1)) if m else None
    base = str(path.parent / VERSION_RE.sub("", path.stem))
    meta = {
        "source": rel,
        "file_name": path.name,
        "category": rel.split("/")[0] if "/" in rel else "uploads",
        "file_type": path.suffix.lower().lstrip("."),
        "last_modified": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        "author": _author(path),
        "version": version,
        "status": "archived" if version is not None and version < latest_versions.get(base, version) else "active",
    }
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        pages = [(i + 1, (p.extract_text() or "").strip()) for i, p in enumerate(PdfReader(path).pages[:50])]
        parts = [(f"{rel}#p{n}", text, {**meta, "page": n}) for n, text in pages if text]
    else:
        parts = [(rel, extract_text(path), {**meta, "page": None})]
    docs = []
    for doc_id, text, md in parts:
        doc = LIDocument(id_=doc_id, text=text, metadata=md)
        doc.excluded_embed_metadata_keys = EMBED_EXCLUDED
        doc.excluded_llm_metadata_keys = LLM_EXCLUDED
        docs.append(doc)
    return docs


def collect_files(paths: list[Path]) -> list[Path]:
    files = []
    for p in paths:
        if p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SUPPORTED
                      and not SKIP_DIRS & set(f.relative_to(p).parts[:-1])]
        elif p.suffix.lower() in SUPPORTED:
            files.append(p)
    return sorted(files)


def build_pipeline(settings: Settings) -> IngestionPipeline:
    from app.services.embeddings import get_embedding_service
    from app.services.rag import ServiceEmbedding

    key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    vector_store = QdrantVectorStore(
        client=QdrantClient(url=settings.qdrant_url, api_key=key),
        aclient=AsyncQdrantClient(url=settings.qdrant_url, api_key=key),
        collection_name=settings.rag_collection,
    )
    embed = ServiceEmbedding(get_embedding_service())
    docstore_dir = settings.docstore_dir
    docstore = (SimpleDocumentStore.from_persist_dir(str(docstore_dir))
                if (docstore_dir / "docstore.json").exists() else SimpleDocumentStore())
    return IngestionPipeline(
        transformations=[build_splitter(settings.rag_chunk_strategy, embed, settings.rag_chunk_size,
                                        settings.rag_chunk_overlap), embed],
        vector_store=vector_store,
        docstore=docstore,
        docstore_strategy=DocstoreStrategy.UPSERTS,
    )


def run_ingestion(paths: list[Path], settings: Settings | None = None) -> IngestReport:
    settings = settings or get_settings()
    data_dir = settings.data_dir.resolve()
    files = collect_files([p.resolve() for p in paths])
    report = IngestReport(files_total=len(files))
    latest = _versions(collect_files([data_dir]))

    documents = []
    for f in files:
        try:
            documents += file_to_documents(f, data_dir, latest)
        except Exception as e:  # noqa: BLE001 — один битый файл не должен ронять всю загрузку
            failed = f.with_name(f.name + ".failed")
            f.rename(failed)
            report.failed.append(failed.relative_to(data_dir).as_posix())
            log.error("ingestion.file_failed", file=f.relative_to(data_dir).as_posix(), error=f"{type(e).__name__}: {e}"[:200])
    report.documents = len(documents)

    pipeline = build_pipeline(settings)
    # Docstore — лишь кеш хешей. Если коллекции в Qdrant нет или она пуста (удалили, новый сервер),
    # верить ему нельзя: он скажет «всё без изменений», и база останется пустой (поймано на практике).
    qdrant = pipeline.vector_store.client
    collection_exists = qdrant.collection_exists(settings.rag_collection)
    if not collection_exists or qdrant.count(settings.rag_collection).count == 0:
        pipeline.docstore = SimpleDocumentStore()
        log.warning("ingestion.docstore_reset", reason="коллекция отсутствует или пуста")
    before = {d.id_: pipeline.docstore.get_document_hash(d.id_) for d in documents}
    changed = [d for d in documents if before[d.id_] != d.hash]
    report.changed = len(changed)
    report.unchanged = report.documents - report.changed
    # UPSERTS удаляет старые ноды документа, только если он есть в ЭТОМ docstore. Docstore хоста и
    # контейнера разные — без явного удаления документ, проиндексированный «с другой стороны», задваивался
    # (поймано: 112 точек вместо 56). Удаляем по doc_id сами — идемпотентно при любом состоянии docstore.
    if collection_exists:
        for d in changed:
            pipeline.vector_store.delete(ref_doc_id=d.id_)
    nodes = pipeline.run(documents=documents, show_progress=False)
    report.nodes_written = len(nodes)
    settings.docstore_dir.mkdir(parents=True, exist_ok=True)
    pipeline.docstore.persist(str(settings.docstore_dir / "docstore.json"))
    log.info("ingestion.done", files=report.files_total, documents=report.documents, changed=report.changed,
             unchanged=report.unchanged, nodes=report.nodes_written, failed=len(report.failed))
    return report


def main() -> None:
    targets = [Path(a) for a in sys.argv[1:]] or [get_settings().data_dir]
    r = run_ingestion(targets)
    print(f"файлов: {r.files_total}, документов (PDF — постранично): {r.documents}; "
          f"{r.changed} changed, {r.unchanged} unchanged; чанков записано: {r.nodes_written}; "
          f"ошибок: {len(r.failed)} {r.failed or ''}")


if __name__ == "__main__":
    main()
