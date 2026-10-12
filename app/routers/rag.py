"""
POST /rag/query — одношаговый ответ по базе знаний (Б5.3, расширен в Б5.5: цитаты [1], [2] и confident).

Отдельный эндпоинт, а не /chat: /chat — обычный LLM-ответ из М3. Диалоговый RAG — на /chats/{id}/messages.
Индекс подключается ОДИН раз в lifespan (app.state.rag). LlamaIndex/re-ranker синхронные — в поток.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/rag", tags=["rag"])


class RAGQuery(BaseModel):
    question: str = Field(min_length=3, max_length=1000, examples=["Сколько можно вернуть за лечение зубов?"])


class RAGSource(BaseModel):
    id: int = Field(description="Номер фрагмента — на него ссылаются [1], [2] в ответе")
    file_name: str | None
    page: int | None = Field(description="Страница для PDF")
    score: float = Field(description="Косинусная близость вектора")
    rerank_score: float | None = Field(default=None, description="Оценка cross-encoder (0..1)")
    snippet: str


class RAGAnswer(BaseModel):
    answer: str
    top_score: float
    confident: bool = Field(description="Уверенный ответ: фрагмент прошёл порог re-ranker/вектора с запасом")
    sources: list[RAGSource]


@router.post("/query", response_model=RAGAnswer, summary="Ответ по базе знаний (RAG) с цитатами",
             responses={503: {"description": "RAG-индекс не инициализирован (Qdrant/эмбеддинги недоступны)"}})
async def rag_query(body: RAGQuery, request: Request) -> RAGAnswer:
    rag = getattr(request.app.state, "rag", None)
    if rag is None:
        raise HTTPException(status_code=503, detail="RAG недоступен")
    result = await asyncio.to_thread(rag.answer, body.question)
    return RAGAnswer(**result)
