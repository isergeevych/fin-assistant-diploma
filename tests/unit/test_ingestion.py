"""Б5.5 — ingestion: метаданные, .failed для битых файлов, идемпотентность при чужом/пустом docstore.
Векторная БД и эмбеддинги заменены фейками — тест без сети."""

from types import SimpleNamespace

from llama_index.core.storage.docstore import SimpleDocumentStore

from app.core.config import LLMSettings, Settings
from app.services import ingestion


class FakeQdrant:
    def __init__(self, exists=True, count=10):
        self._exists, self._count = exists, count

    def collection_exists(self, name):
        return self._exists

    def count(self, name):
        return SimpleNamespace(count=self._count)


class FakeVectorStore:
    def __init__(self, qdrant):
        self.client = qdrant
        self.deleted: list[str] = []

    def delete(self, ref_doc_id):
        self.deleted.append(ref_doc_id)


class FakePipeline:
    def __init__(self, docstore, qdrant):
        self.docstore = docstore
        self.vector_store = FakeVectorStore(qdrant)
        self.ran_with: list = []

    def run(self, documents, show_progress=False):
        self.ran_with = documents
        for d in documents:
            self.docstore.set_document_hash(d.id_, d.hash)
        return documents


def setup(tmp_path, monkeypatch, docstore=None, qdrant=None):
    data = tmp_path / "data"
    (data / "app_faq").mkdir(parents=True)
    (data / "app_faq" / "tariffs_v1.md").write_text("# Старый тариф\n\n249 ₽", encoding="utf-8")
    (data / "app_faq" / "tariffs_v2.md").write_text("# Новый тариф\n\n299 ₽", encoding="utf-8")
    (data / "app_faq" / "broken.pdf").write_bytes(b"%PDF-1.4 definitely not a real pdf")
    settings = Settings(llm=LLMSettings(api_key="t"), data_dir=data, docstore_dir=tmp_path / "docstore")
    pipeline = FakePipeline(docstore or SimpleDocumentStore(), qdrant or FakeQdrant())
    monkeypatch.setattr(ingestion, "build_pipeline", lambda s: pipeline)
    return data, settings, pipeline


def test_metadata_failed_files_and_idempotency(tmp_path, monkeypatch):
    data, settings, pipeline = setup(tmp_path, monkeypatch)
    report = ingestion.run_ingestion([data], settings)

    assert report.failed == ["app_faq/broken.pdf.failed"] and (data / "app_faq" / "broken.pdf.failed").exists()
    by_id = {d.id_: d for d in pipeline.ran_with}
    assert by_id["app_faq/tariffs_v1.md"].metadata["status"] == "archived"
    assert by_id["app_faq/tariffs_v2.md"].metadata["status"] == "active"
    assert by_id["app_faq/tariffs_v2.md"].metadata["category"] == "app_faq"
    assert "source" in by_id["app_faq/tariffs_v2.md"].excluded_embed_metadata_keys  # шум не идёт в эмбеддинг
    assert report.changed == 2

    second = ingestion.run_ingestion([data], settings)  # тот же docstore
    assert (second.changed, second.unchanged) == (0, 2)


def test_changed_document_removes_old_nodes_even_with_foreign_docstore(tmp_path, monkeypatch):
    # Пустой docstore, но коллекция с данными есть (проиндексировано «с другой стороны»)
    data, settings, pipeline = setup(tmp_path, monkeypatch, qdrant=FakeQdrant(exists=True, count=56))
    ingestion.run_ingestion([data], settings)
    assert set(pipeline.vector_store.deleted) == {"app_faq/tariffs_v1.md", "app_faq/tariffs_v2.md"}


def test_stale_docstore_is_ignored_when_collection_is_empty(tmp_path, monkeypatch):
    stale = SimpleDocumentStore()
    data, settings, pipeline = setup(tmp_path, monkeypatch, docstore=stale, qdrant=FakeQdrant(exists=False))
    # docstore «помнит» документы, а коллекции нет — должны переиндексировать, а не пропустить
    for name in ("tariffs_v1.md", "tariffs_v2.md"):
        doc = ingestion.file_to_documents(data / "app_faq" / name, data, {})[0]
        stale.set_document_hash(doc.id_, doc.hash)
    report = ingestion.run_ingestion([data], settings)
    assert report.changed == 2 and pipeline.vector_store.deleted == []  # удалять из несуществующей коллекции нельзя
