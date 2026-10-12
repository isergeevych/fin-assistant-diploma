"""
Б4.4 — admin API (только Postgres).

  GET  /chats/admin/stats        метрики за 24 ч
  GET  /chats/admin/users        последние пользователи
  POST /chats/admin/broadcast    поставить рассылку в очередь (бот заберёт в фоне)
Защита — X-Admin-Token (compare_digest). Полноценный JWT — за рамками ДЗ.

  GET  /internal/broadcasts/pending          бот забирает рассылки + получателей  (X-Internal-Token)
  POST /internal/broadcasts/{id}/done        бот отмечает отправку                  (X-Internal-Token)

Роутер подключается в main.py РАНЬШЕ чат-роутера: иначе /chats/admin/stats совпал бы с
/chats/{chat_id} и «admin» упал бы на валидации UUID (422).
"""

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, distinct, func, select, update

from app.chat.deps import DbSessionDep
from app.chat.repositories.pg_models import (
    BroadcastRow,
    ChatMessageRow,
    ChatRow,
    MessageFeedbackRow,
    ModerationIncidentRow,
)
from app.deps.auth import require_admin, require_internal
from app.observability.pii import redact_pii

router = APIRouter(prefix="/chats/admin", tags=["admin"], dependencies=[Depends(require_admin)])
internal = APIRouter(prefix="/internal/broadcasts", tags=["internal"], dependencies=[Depends(require_internal)])


_PUNCT = re.compile(r"[^\w\s\[\]]+")  # плейсхолдеры [PHONE_RU] сохраняем


def _normalize(text: str) -> str:
    return " ".join(_PUNCT.sub("", text.lower()).split())


class TopQuestion(BaseModel):
    question: str
    count: int


class StatsOut(BaseModel):
    period_hours: int = 24
    total_messages: int
    active_users: int
    avg_latency_ms: float | None
    moderation_block_rate: float
    feedback_up_ratio: float | None
    feedback_total: int
    top_questions: list[TopQuestion]


class UserOut(BaseModel):
    owner_external_id: str
    interface: str
    chats: int
    last_seen_at: datetime | None


class BroadcastIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    interface_filter: Literal["telegram"] = "telegram"


class PendingBroadcast(BaseModel):
    id: int
    message: str
    recipients: list[str]


class BroadcastDone(BaseModel):
    sent_count: int = Field(ge=0)


@router.get("/stats", response_model=StatsOut, summary="Метрики за 24 часа")
async def stats(session: DbSessionDep) -> StatsOut:
    since = datetime.now(UTC) - timedelta(hours=24)
    m = ChatMessageRow
    recent = m.created_at >= since

    total = await session.scalar(select(func.count()).select_from(m).where(recent))
    user_msgs = await session.scalar(select(func.count()).select_from(m).where(recent, m.role == "user"))
    active = await session.scalar(
        select(func.count(distinct(ChatRow.owner_external_id)))
        .join(m, m.chat_id == ChatRow.id).where(recent, m.role == "user"))
    avg_latency = await session.scalar(select(func.avg(m.latency_ms)).where(recent, m.role == "assistant"))
    blocked = await session.scalar(
        select(func.count()).select_from(ModerationIncidentRow)
        .where(ModerationIncidentRow.created_at >= since, ModerationIncidentRow.direction == "input"))
    fb = (await session.execute(
        select(func.count(), func.sum(case((MessageFeedbackRow.value == "up", 1), else_=0)))
        .where(MessageFeedbackRow.created_at >= since))).one()

    # Топ вопросов: СНАЧАЛА маскируем PII, ПОТОМ нормализуем. В SQL (lower(regexp_replace(...)))
    # порядок обратный: пунктуация уходит, «+7 999 123-45-67» становится «7 999 1234567», маскер
    # его уже не узнаёт — и админ видел бы телефон (поймано тестом). Поэтому считаем в Python.
    contents = (await session.scalars(
        select(m.content).where(recent, m.role == "user").order_by(m.created_at.desc()).limit(5000))).all()
    top = Counter(_normalize(redact_pii(c)) for c in contents).most_common(5)

    attempts = (user_msgs or 0) + (blocked or 0)
    return StatsOut(
        total_messages=total or 0,
        active_users=active or 0,
        avg_latency_ms=round(float(avg_latency), 1) if avg_latency is not None else None,
        moderation_block_rate=round((blocked or 0) / attempts, 4) if attempts else 0.0,
        feedback_up_ratio=round((fb[1] or 0) / fb[0], 4) if fb[0] else None,
        feedback_total=fb[0] or 0,
        top_questions=[TopQuestion(question=q[:200], count=n) for q, n in top],
    )


@router.get("/users", response_model=list[UserOut], summary="Последние пользователи")
async def users(session: DbSessionDep, limit: int = Query(default=50, ge=1, le=500)) -> list[UserOut]:
    last_seen = func.max(ChatMessageRow.created_at)
    rows = (await session.execute(
        select(ChatRow.owner_external_id, ChatRow.interface, func.count(distinct(ChatRow.id)), last_seen)
        .outerjoin(ChatMessageRow, ChatMessageRow.chat_id == ChatRow.id)
        .group_by(ChatRow.owner_external_id, ChatRow.interface)
        .order_by(last_seen.desc().nulls_last()).limit(limit))).all()
    return [UserOut(owner_external_id=o, interface=i, chats=c, last_seen_at=s) for o, i, c, s in rows]


@router.post("/broadcast", summary="Поставить рассылку в очередь")
async def broadcast(body: BroadcastIn, session: DbSessionDep) -> dict:
    row = BroadcastRow(message=body.message, interface=body.interface_filter, status="pending")
    session.add(row)
    await session.commit()
    return {"status": "queued", "id": row.id}


@internal.get("/pending", response_model=list[PendingBroadcast], summary="Рассылки для отправки ботом")
async def pending(session: DbSessionDep) -> list[PendingBroadcast]:
    rows = (await session.scalars(
        select(BroadcastRow).where(BroadcastRow.status == "pending").order_by(BroadcastRow.id).limit(5))).all()
    result = []
    for row in rows:
        recipients = (await session.scalars(
            select(distinct(ChatRow.owner_external_id)).where(ChatRow.interface == row.interface))).all()
        result.append(PendingBroadcast(id=row.id, message=row.message, recipients=list(recipients)))
    return result


@internal.post("/{broadcast_id}/done", summary="Отметить рассылку отправленной")
async def done(broadcast_id: int, body: BroadcastDone, session: DbSessionDep) -> dict:
    res = await session.execute(
        update(BroadcastRow).where(and_(BroadcastRow.id == broadcast_id, BroadcastRow.status == "pending"))
        .values(status="sent", sent_at=func.now(), sent_count=body.sent_count))
    await session.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="рассылка не найдена или уже отправлена")
    return {"status": "ok"}

