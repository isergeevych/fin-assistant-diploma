"""
Б6.4 — POST /agent/stream: SSE-стрим персистентного графа с human-in-the-loop.

    {"thread_id": "tg-1845608632", "message": "Отправь отчёт за сентябрь на me@example.com"}  # новый ход
    {"thread_id": "tg-1845608632", "resume": true}                                            # решение по паузе

graph.astream(stream_mode=["updates", "messages"]):
  updates  → {"type": "update", "node": …}  — прогресс по узлам (что сделал агент);
  messages → {"type": "token", "delta": …}  — токены ответа модели по мере генерации;
  пауза    → {"type": "interrupt", "value": {…черновик…}} — ждём resume;
  конец    → {"type": "done", "next": [...]}  — next непустой, если граф стоит на паузе.

thread_id задаёт клиент и он стабилен (для бота — tg-{chat_id}): по нему checkpointer находит историю.
Роль определяет СЕРВЕР: валидный X-Admin-Token → full, иначе AGENT_DEFAULT_ROLE — клиент её не выбирает.
"""

import secrets
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, BaseMessage
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator

from app.core.config import get_settings
from app.core.sse import sse_event
from app.services.security.input_validator import validate_input

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentStreamRequest(BaseModel):
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{3,64}$", examples=["tg-1845608632"])
    message: str | None = Field(default=None, max_length=4000)
    resume: bool | None = Field(default=None, description="Решение по паузе: true — отправить, false — отменить")

    @model_validator(mode="after")
    def one_of(self):
        if (self.message is None) == (self.resume is None):
            raise ValueError("нужно ровно одно из полей: message или resume")
        return self


def _role(x_admin_token: str) -> str:
    s = get_settings()
    expected = s.admin_token.get_secret_value() if s.admin_token else ""
    if expected and secrets.compare_digest(x_admin_token.encode(), expected.encode()):
        return "full"
    return s.agent_default_role


def _summarize(update: dict | None) -> dict:
    """Сообщения LangChain не сериализуются в JSON как есть — отдаём суть."""
    out = {}
    for key, value in (update or {}).items():
        if key == "messages":
            out[key] = [{"type": m.type, "content": str(m.content)[:300],
                         "tool_calls": [tc["name"] for tc in getattr(m, "tool_calls", [])]}
                        for m in value if isinstance(m, BaseMessage)]
        elif key in ("sent", "iteration_count"):
            out[key] = value
        elif key == "email_draft":
            out[key] = {k: value[k] for k in ("to", "subject")} if value else None
    return out


async def _events(graph, payload, config) -> AsyncIterator[str]:
    async for mode, data in graph.astream(payload, config, stream_mode=["updates", "messages"]):
        if mode == "messages":
            chunk, meta = data
            if isinstance(chunk, AIMessageChunk) and chunk.content and meta.get("langgraph_node") == "call_model":
                yield sse_event("token", delta=chunk.content)
            continue
        for node, update in data.items():
            if node == "__interrupt__":
                for intr in update:
                    yield sse_event("interrupt", value=intr.value)
            else:
                yield sse_event("update", node=node, **_summarize(update))
    snapshot = await graph.aget_state(config)
    yield sse_event("done", next=list(snapshot.next), sent=snapshot.values.get("sent"))


@router.post("/stream", summary="Агент с подтверждением опасных действий (SSE)")
async def agent_stream(body: AgentStreamRequest, request: Request, x_admin_token: str = Header(default="")):
    graph = request.app.state.agent_graph
    if graph is None:
        raise HTTPException(status_code=503, detail="agent unavailable")
    config = {"configurable": {"thread_id": body.thread_id, "user_role": _role(x_admin_token)}}
    if body.resume is not None:
        snapshot = await graph.aget_state(config)
        if not snapshot.next:
            raise HTTPException(status_code=409, detail="нет паузы, ожидающей решения, в этом треде")
        payload = Command(resume=body.resume)
    else:
        verdict = validate_input(body.message)
        if not verdict.ok:
            raise HTTPException(status_code=400, detail={"code": "input_rejected", "rule": verdict.rule})
        # iteration_count=0 — лимит шагов считается на один ход пользователя, а не на весь тред
        payload = {"messages": [{"role": "user", "content": body.message}], "iteration_count": 0}
    return StreamingResponse(_events(graph, payload, config), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
