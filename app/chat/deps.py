"""
DI модуля чата (Б4.1): какое хранилище использовать — решает CHAT_REPOSITORY
(json | postgres) из настроек. Глобальных сервисов на уровне модуля нет.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request

from app.chat.repositories.json_repo import JsonChatRepository
from app.chat.repositories.pg_repo import PostgresChatRepository
from app.chat.repository import ChatRepository
from app.chat.service import ChatService
from app.deps.providers import LLMClientDep, SettingsDep, get_canary
from app.prompts.loader import render_template
from app.services.security.guard import SecurityGuard


async def get_repository(request: Request, settings: SettingsDep) -> AsyncIterator[ChatRepository]:
    if settings.chat_repository == "json":
        yield JsonChatRepository(base_dir=settings.chat_storage_dir)
    elif settings.chat_repository == "postgres":
        sessionmaker = getattr(request.app.state, "db_sessionmaker", None)
        if sessionmaker is None:
            raise RuntimeError("CHAT_REPOSITORY=postgres, но БД не инициализирована в lifespan")
        async with sessionmaker() as session:  # сессия живёт ровно один запрос (включая SSE-стрим)
            yield PostgresChatRepository(session)
    else:
        raise ValueError(f"CHAT_REPOSITORY={settings.chat_repository!r}: ожидается 'json' или 'postgres'")


def get_chat_service(
    repo: Annotated[ChatRepository, Depends(get_repository)],
    llm: LLMClientDep,
    settings: SettingsDep,
    canary: Annotated[str, Depends(get_canary)],
) -> ChatService:
    guard = SecurityGuard(
        settings.security_enabled,
        render_template("chat_system_v1.j2", product_name=settings.product_name),
        canary,
    )
    return ChatService(repo, llm, settings, guard)


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]
