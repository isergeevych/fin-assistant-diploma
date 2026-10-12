"""
Б5.6 — метрики качества RAG (RAGAS 0.4, ragas.metrics.collections) + кастомная has_citation.

  Faithfulness      — все ли утверждения ответа подтверждаются найденными фрагментами (галлюцинации ↓);
  AnswerRelevancy   — по ответу генерируются вопросы и сравниваются с исходным (эмбеддинги);
  ContextPrecision  — релевантные фрагменты стоят выше нерелевантных (нужен reference);
  ContextRecall     — покрыт ли reference найденными фрагментами (нужен reference);
  has_citation      — есть ли в ответе ссылка на источник ([1], имя файла, «согласно …»).

Судья — llm_factory поверх OpenAI-совместимого клиента: локально qwen3:8b (Ollama), в облаке —
модель сильнее production (EVAL_JUDGE_MODEL). При смене судьи старые результаты НЕ сравнимы — пересчёт.
У reasoning-моделей (qwen3) «размышления» выключаются: иначе instructor упирается в max_tokens.
"""

import time
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel
from ragas.embeddings import OpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.metrics import discrete_metric
from ragas.metrics.collections import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

from app.core.config import Settings

CITATION_PROMPT = (
    "Содержит ли ответ ссылку на источник: маркер вида '[1]'/'[doc_id]', имя файла, или фразу 'согласно …'? "
    "Ответ «по базе не нашёл» без утверждений тоже считай корректным (yes) — ссылаться не на что.\n"
    "Верни verdict: yes или no.\n\nОтвет:\n{response}"
)


class CitationVerdict(BaseModel):
    verdict: Literal["yes", "no"]


def build_judge(settings: Settings):
    base_url = settings.eval_judge_base_url or settings.rag_llm_base_url or settings.llm.base_url
    key = (settings.eval_judge_api_key or settings.rag_llm_api_key or settings.llm.api_key).get_secret_value()
    model = settings.eval_judge_model or settings.rag_llm_model or settings.llm.default_model
    extra = {"extra_body": {"reasoning_effort": settings.rag_llm_reasoning_effort}} if settings.rag_llm_reasoning_effort else {}
    return llm_factory(model, provider="openai", client=AsyncOpenAI(base_url=base_url, api_key=key), max_tokens=3000,
                       **extra), model


def build_embeddings(settings: Settings) -> OpenAIEmbeddings:
    client = AsyncOpenAI(base_url=settings.embedding_base_url, api_key=settings.embedding_api_key.get_secret_value())
    return OpenAIEmbeddings(client=client, model=settings.embedding_model)


def make_has_citation(judge):
    @discrete_metric(name="has_citation", allowed_values=["yes", "no"])
    async def has_citation(response: str) -> str:
        result = await judge.agenerate(CITATION_PROMPT.format(response=response), response_model=CitationVerdict)
        return result.verdict

    return has_citation


def build_metrics(judge, embeddings) -> dict:
    return {
        "faithfulness": Faithfulness(llm=judge),
        "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=judge),
        "context_recall": ContextRecall(llm=judge),
        "has_citation": make_has_citation(judge),
    }


async def eval_row(metrics: dict, user_input: str, response: str, contexts: list[str], reference: str) -> dict:
    """Аргументы только именованные, у метрик разный набор полей. Ошибка одной метрики не валит строку."""
    calls = {
        "faithfulness": dict(user_input=user_input, response=response, retrieved_contexts=contexts or [""]),
        "answer_relevancy": dict(user_input=user_input, response=response),
        "context_precision": dict(user_input=user_input, reference=reference, retrieved_contexts=contexts or [""]),
        "context_recall": dict(user_input=user_input, retrieved_contexts=contexts or [""], reference=reference),
        "has_citation": dict(response=response),
    }
    row: dict = {}
    started = time.perf_counter()
    for name, kwargs in calls.items():
        try:
            result = await metrics[name].ascore(**kwargs)
            value = result.value
            row[name] = (1.0 if value == "yes" else 0.0) if name == "has_citation" else float(value)
        except Exception as e:  # noqa: BLE001
            row[name] = None
            row[f"{name}_error"] = f"{type(e).__name__}: {e}"[:200]
    row["judge_seconds"] = round(time.perf_counter() - started, 1)
    return row
