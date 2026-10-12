"""
Б5.3 — POST /rag/query: одношаговый ответ по базе знаний.

Отдельный эндпоинт, а не /chat: /chat — обычный LLM-ответ из М3. Индекс создаётся ОДИН раз в lifespan
(app.state.rag), не на каждый запрос. LlamaIndex здесь синхронный — вызов уходит в поток,
чтобы не блокировать event loop.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/rag", tags=["rag"])


class RAGQuery(BaseModel):
    question: str = Field(min_length=3, max_length=1000, examples=["Сколько можно вернуть за лечение зубов?"])


class RAGSource(BaseModel):
    text: str
    source: str | None
    score: float


class RAGAnswer(BaseModel):
    answer: str
    top_score: float
    sources: list[RAGSource]


@router.post("/query", response_model=RAGAnswer, summary="Ответ по базе знаний (RAG)",
             responses={503: {"description": "RAG-индекс не инициализирован (Qdrant/эмбеддинги недоступны)"}})
async def rag_query(body: RAGQuery, request: Request) -> RAGAnswer:
    rag = getattr(request.app.state, "rag", None)
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG недоступен")
    result = await asyncio.to_thread(rag.answer, body.question)
    return RAGAnswer(**result)
