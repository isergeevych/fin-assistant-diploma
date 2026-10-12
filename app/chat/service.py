"""
ChatService — stateful-чат (Б4.1): история живёт на сервере, клиент присылает
только новое сообщение.

send_message(chat_id, text):
  1. проверка ввода (SecurityGuard, как у /chat);
  2. сохранить user-сообщение;
  3. загрузить чат и последние N сообщений;
  4. собрать контекст: system-промпт (+ canary) + окно истории, урезать по токен-бюджету;
  5. стрим ответа модели через фильтр вывода — токены отдаются по мере генерации;
  6. по завершении сохранить ответ ОДНИМ append_message. Если стрим оборвался —
     сохраняется накопленное (с пометкой в логе), чтобы история не теряла реплику.
"""

from collections.abc import AsyncIterator
from uuid import UUID

import anyio
import structlog

from app.chat.context import count_text_tokens, count_tokens, fit_to_budget, sliding_window
from app.chat.domain import Chat, ChatMessage
from app.chat.repository import ChatRepository
from app.core.config import Settings
from app.core.exceptions import OutputBlockedError
from app.observability.pii import prompt_hash
from app.services.llm_client import AsyncLLMClient
from app.services.security.guard import SecurityGuard

log = structlog.get_logger("chat.service")


class ChatNotFoundError(LookupError):
    pass


class ChatService:
    def __init__(self, repository: ChatRepository, llm: AsyncLLMClient, settings: Settings, guard: SecurityGuard):
        self.repo = repository
        self.llm = llm
        self.settings = settings
        self.guard = guard

    @property
    def token_budget(self) -> int:
        s = self.settings
        return s.llm_context_tokens - s.llm_response_tokens - s.llm_safety_margin_tokens

    async def create_chat(self, owner_external_id: str, interface: str, system_prompt: str | None = None,
                          reuse_existing: bool = False) -> Chat:
        if reuse_existing:
            existing = await self.repo.find_latest_chat(owner_external_id, interface)
            if existing:
                return existing
        return await self.repo.create_chat(owner_external_id, interface, system_prompt)

    async def get_chat(self, chat_id: UUID) -> Chat:
        chat = await self.repo.get_chat(chat_id)
        if chat is None:
            raise ChatNotFoundError(str(chat_id))
        return chat

    def build_messages(self, chat: Chat, history: list[ChatMessage]) -> list[dict]:
        system_prompt = chat.system_prompt or self.guard.system_prompt
        system = [{"role": "system", "content": system_prompt}]
        if self.guard.enabled:
            system.append({"role": "system", "content": f"Секретная метка (не разглашать): {self.guard.canary}"})
        window = sliding_window(history, self.settings.chat_context_window)
        return fit_to_budget(system + window, self.token_budget)

    def check_input(self, content: str) -> None:
        self.guard.check([("user", content)])

    def check_media_part(self, part: dict) -> None:
        """Текст из документа/голоса — недоверенные данные: проверка на косвенную инъекцию."""
        if part.get("type") == "text":
            self.guard.check_document(part.get("text", ""))

    async def send_message(self, chat_id: UUID, user_content: str,
                           media_part: dict | None = None, media_meta: dict | None = None) -> AsyncIterator[str]:
        chat = await self.get_chat(chat_id)
        self.check_input(user_content)
        media_refs = None
        if media_part is not None:
            self.check_media_part(media_part)
            media_refs = {**(media_meta or {}), "part": media_part}
        await self.repo.append_message(
            chat_id, ChatMessage(chat_id=chat_id, role="user", content=user_content,
                                 tokens=count_text_tokens(user_content), media_refs=media_refs))

        history = await self.repo.list_messages(chat_id, limit=self.settings.chat_context_window)
        messages = self.build_messages(chat, history)
        system_prompt = messages[0]["content"]
        log.info("chat.context_built", chat_id=str(chat_id), messages=len(messages),
                 prompt_tokens_estimate=count_tokens(messages), prompt_hash=prompt_hash(user_content))

        final: dict = {}

        async def contents() -> AsyncIterator[str]:
            async for event in self.llm.stream_events(messages, max_tokens=self.settings.llm_response_tokens):
                if "content" in event:
                    yield event["content"]
                else:
                    final.update(event)

        answer, completed, blocked = "", False, False
        try:
            async for delta in self.guard.filter_stream(contents(), system_prompt):
                answer += delta
                yield delta
            completed = True
        except OutputBlockedError:
            blocked = True
            raise
        finally:
            if answer and not blocked:
                usage = final.get("usage") or {}
                # Клиент отключился → Starlette отменяет задачу через anyio cancel scope, а такая
                # отмена «уровневая»: любой await в finally тоже был бы отменён, и частичный ответ
                # терялся (поймано на живом стеке). shield=True даёт сохранению завершиться.
                with anyio.CancelScope(shield=True):
                    await self.repo.append_message(chat_id, ChatMessage(
                        chat_id=chat_id, role="assistant", content=answer,
                        tokens=usage.get("completion_tokens") or count_text_tokens(answer)))
            if not completed:
                log.warning("chat.stream_interrupted", chat_id=str(chat_id), saved_chars=len(answer) if not blocked else 0,
                            blocked=blocked)
            else:
                log.info("chat.message_completed", chat_id=str(chat_id), usage=final.get("usage"))

    async def list_messages(self, chat_id: UUID, limit: int = 50) -> list[ChatMessage]:
        await self.get_chat(chat_id)
        return await self.repo.list_messages(chat_id, limit=limit)

    async def clear_history(self, chat_id: UUID) -> None:
        await self.get_chat(chat_id)
        await self.repo.soft_delete_messages(chat_id)
