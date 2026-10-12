"""
DI модуля чата (Б4.1, Б4.4): какое хранилище использовать — решает CHAT_REPOSITORY
(json | postgres) из настроек. Глобальных сервисов на уровне модуля нет.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.repositories.json_repo import JsonChatRepository
from app.chat.repositories.pg_models import ModerationIncidentRow
from app.chat.repositories.pg_repo import PostgresChatRepository
from app.chat.repository import ChatRepository
from app.chat.service import ChatService, IncidentSink
from app.deps.providers import LLMClientDep, SettingsDep, get_canary
from app.moderation.service import ModerationResult, ModerationService
from app.observability.pii import preview, prompt_hash
from app.prompts.loader import render_template
from app.services.security.guard import SecurityGuard


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker = getattr(request.app.state, "db_sessionmaker", None)
    if sessionmaker is None:
        raise HTTPException(status_code=501, detail="Функция требует CHAT_REPOSITORY=postgres")
    async with sessionmaker() as session:  # сессия живёт ровно один запрос (включая SSE-стрим)
        yield session


async def _optional_session(request: Request) -> AsyncIterator[AsyncSession | None]:
    sessionmaker = getattr(request.app.state, "db_sessionmaker", None)
    if sessionmaker is None:
        yield None
        return
    async with sessionmaker() as session:
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
OptionalSessionDep = Annotated[AsyncSession | None, Depends(_optional_session)]


async def get_repository(settings: SettingsDep, session: OptionalSessionDep) -> ChatRepository:
    if settings.chat_repository == "json":
        return JsonChatRepository(base_dir=settings.chat_storage_dir)
    if settings.chat_repository == "postgres":
        if session is None:
            raise RuntimeError("CHAT_REPOSITORY=postgres, но БД не инициализирована в lifespan")
        return PostgresChatRepository(session)
    raise ValueError(f"CHAT_REPOSITORY={settings.chat_repository!r}: ожидается 'json' или 'postgres'")


def get_moderation(settings: SettingsDep) -> ModerationService:
    client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()) if settings.openai_api_key else None
    return ModerationService(openai_client=client, enabled=settings.moderation_enabled)


def get_incident_sink(session: OptionalSessionDep) -> IncidentSink | None:
    if session is None:
        return None  # JSON-режим: инциденты только в логах

    async def sink(owner: str, direction: str, result: ModerationResult, text: str) -> None:
        session.add(ModerationIncidentRow(owner_external_id=owner, direction=direction, layer=result.blocked_by,
                                          categories=result.categories, text_hash=prompt_hash(text),
                                          text_preview=preview(text)))
        await session.commit()

    return sink


def get_chat_service(
    request: Request,
    repo: Annotated[ChatRepository, Depends(get_repository)],
    llm: LLMClientDep,
    settings: SettingsDep,
    canary: Annotated[str, Depends(get_canary)],
    moderation: Annotated[ModerationService, Depends(get_moderation)],
    sink: Annotated[IncidentSink | None, Depends(get_incident_sink)],
) -> ChatService:
    guard = SecurityGuard(
        settings.security_enabled,
        render_template("chat_system_v1.j2", product_name=settings.product_name),
        canary,
    )
    rag = getattr(request.app.state, "rag", None) if settings.chat_use_rag else None
    return ChatService(repo, llm, settings, guard, moderation, sink, rag=rag)


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
