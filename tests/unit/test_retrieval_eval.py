"""Б5.4 — метрики retrieval на ручных примерах."""

from app.services.chunking import ru_sentences
from app.services.retrieval_eval import dedupe, evaluate, hit_rate, recall, reciprocal_rank


def test_dedupe_keeps_first_occurrence_order():
    assert dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_metrics_by_hand():
    ranked = ["x", "y", "rel1", "z", "rel2"]
    rel = {"rel1", "rel2", "rel3"}
    assert hit_rate(ranked, rel, k=5) == 1.0 and hit_rate(ranked, rel, k=2) == 0.0
    assert reciprocal_rank(ranked, rel, k=10) == 1 / 3
    assert recall(ranked, rel, k=10) == 2 / 3


def test_chunks_of_one_document_take_one_rank():
    dataset = [{"id": "q", "question": "?", "relevant_doc_ids": ["doc2"]}]
    # doc1 вернулся тремя чанками подряд — после схлопывания doc2 на 2-м месте, а не на 4-м
    report = evaluate(dataset, lambda q: ["doc1", "doc1", "doc1", "doc2"])
    assert report.mrr_at_10 == 0.5 and report.hit_rate_at_5 == 1.0


def test_ru_sentence_splitter_keeps_numbers_and_abbreviations():
    text = "Лимит — 1,4 млн ₽. Выплата через 14 дней, т. е. быстро. «Кавычки» тоже начало."
    assert ru_sentences(text) == ["Лимит — 1,4 млн ₽.", "Выплата через 14 дней, т. е. быстро.", "«Кавычки» тоже начало."]
