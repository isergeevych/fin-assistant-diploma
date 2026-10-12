"""
ChatService — stateful-чат (Б4.1): история живёт на сервере, клиент присылает
только новое сообщение.

send_message(chat_id, text, media):
  1. проверки до LLM: фильтр атак (SecurityGuard, Б3.8) и модерация контента (Б4.4) —
     вызываются из роутера ДО начала стрима, чтобы отказ пришёл честным HTTP-кодом;
  2. сохранить user-сообщение (+ media_refs, Б4.3);
  3. загрузить чат и последние N сообщений;
  4. собрать контекст: system-промпт (+ canary) + окно истории, урезать по токен-бюджету;
  5. стрим ответа модели через фильтр вывода — токены отдаются по мере генерации;
  6. модерация финального ответа: нарушение → в историю пишется отказ, клиент получает
     событие «замени текст» (ResponseReplaced);
  7. ответ сохраняется ОДНИМ append_message с заранее известным id (нужен для 👍/👎).
     Если стрим оборвался — сохраняется накопленное (с пометкой в логе).
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID, uuid4

import anyio
import structlog

from app.chat.context import count_text_tokens, count_tokens, fit_to_budget, sliding_window
from app.chat.domain import Chat, ChatMessage
from app.chat.repository import ChatRepository
from app.core.config import Settings
from app.core.exceptions import LLMError, ModerationBlockedError, OutputBlockedError
from app.moderation.service import ModerationResult, ModerationService
from app.observability.pii import prompt_hash
from app.prompts.loader import render_template
from app.services.llm_client import AsyncLLMClient
from app.services.security.guard import SecurityGuard

log = structlog.get_logger("chat.service")

MODERATED_REPLY = "Не могу показать ответ — он мог нарушить правила."
IncidentSink = Callable[[str, str, ModerationResult, str], Awaitable[None]]


class ChatNotFoundError(LookupError):
    pass


class ResponseReplaced(LLMError):
    """Ответ модели не прошёл модерацию вывода: клиент должен заменить показанный текст."""

    code = "moderation_output"
    http_status = 200

    def __init__(self, replacement: str):
        super().__init__(replacement)
        self.replacement = replacement


class ChatService:
    def __init__(self, repository: ChatRepository, llm: AsyncLLMClient, settings: Settings, guard: SecurityGuard,
                 moderation: ModerationService | None = None, incident_sink: IncidentSink | None = None,
                 rag=None):
        self.repo = repository
        self.rag = rag  # RAGService (Б5.5) или None — тогда чат без базы знаний
        self.last_sources: list[dict] = []
        self.llm = llm
        self.settings = settings
        self.guard = guard
        self.moderation = moderation
        self.incident_sink = incident_sink

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

    # ---------- проверки до LLM ----------

    def check_input(self, content: str) -> None:
        self.guard.check([("user", content)])

    def check_media_part(self, part: dict) -> None:
        """Текст из документа/голоса — недоверенные данные: проверка на косвенную инъекцию."""
        if part.get("type") == "text":
            self.guard.check_document(part.get("text", ""))

    async def moderate_input(self, chat: Chat, text: str) -> None:
        if self.moderation is None:
            return
        result = await self.moderation.check_input(text)
        if not result.allowed:
            await self._record_incident(chat.owner_external_id, "input", result, text)
            raise ModerationBlockedError(result.categories)

    async def _record_incident(self, owner: str, direction: str, result: ModerationResult, text: str) -> None:
        if self.incident_sink is not None:
            with anyio.CancelScope(shield=True):
                await self.incident_sink(owner, direction, result, text)

    # ---------- основной поток ----------

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
        self.last_sources = []
        if self.rag is not None:  # Б5.5: диалоговый RAG поверх истории из М4
            messages = await self._with_knowledge(messages, user_content)
        log.info("chat.context_built", chat_id=str(chat_id), messages=len(messages),
                 prompt_tokens_estimate=count_tokens(messages), prompt_hash=prompt_hash(user_content))

        self.last_assistant_message_id = assistant_id = uuid4()
        final: dict = {}
        started = time.perf_counter()

        async def contents() -> AsyncIterator[str]:
            async for event in self.llm.stream_events(messages, max_tokens=self.settings.llm_response_tokens):
                if "content" in event:
                    yield event["content"]
                else:
                    final.update(event)

        answer, completed, blocked, replaced = "", False, False, False
        try:
            async for delta in self.guard.filter_stream(contents(), system_prompt):
                answer += delta
                yield delta
            completed = True
            if self.moderation is not None and answer:
                verdict = await self.moderation.check_output(answer)
                if not verdict.allowed:
                    await self._record_incident(chat.owner_external_id, "output", verdict, answer)
                    replaced = True
                    raise ResponseReplaced(MODERATED_REPLY)
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
                        id=assistant_id, chat_id=chat_id, role="assistant",
                        content=MODERATED_REPLY if replaced else answer,
                        tokens=usage.get("completion_tokens") or count_text_tokens(answer),
                        latency_ms=round((time.perf_counter() - started) * 1000),
                        # показанные источники — для фидбека и разбора ответов (Б5.5)
                        media_refs={"sources": self.last_sources} if self.last_sources else None))
            if not completed:
                log.warning("chat.stream_interrupted", chat_id=str(chat_id),
                            saved_chars=len(answer) if not blocked else 0, blocked=blocked)
            else:
                log.info("chat.message_completed", chat_id=str(chat_id), usage=final.get("usage"),
                         moderated=replaced)

    async def _with_knowledge(self, messages: list[dict], question: str) -> list[dict]:
        """Поиск по базе (condense по истории → retrieve → re-rank → guard) и системное сообщение с
        пронумерованными фрагментами. История уходит в LLM целиком — condense нужен только поиску."""
        dialog = [{"role": m["role"], "content": m["content"]} for m in messages[:-1]
                  if m["role"] in ("user", "assistant") and isinstance(m["content"], str)]
        try:
            search_query = await anyio.to_thread.run_sync(self.rag.condense, question, dialog)
            chunks, top_score = await anyio.to_thread.run_sync(self.rag.retrieve, search_query)
        except Exception as e:  # noqa: BLE001 — база знаний недоступна: отвечаем без неё, а не падаем
            log.warning("chat.rag_unavailable", error=type(e).__name__)
            return messages
        found = self.rag.passes_guard(chunks, top_score)
        log.info("chat.rag", top_score=round(top_score, 3), found=found, chunks=len(chunks) if found else 0)
        if found:
            self.last_sources = self.rag.sources_payload(chunks)
        context = render_template("rag_context.j2", chunks=chunks if found else [])
        system = [m for m in messages if m["role"] == "system"]
        rest = [m for m in messages if m["role"] != "system"]
        return [*system, {"role": "system", "content": context}, *rest]

    async def list_messages(self, chat_id: UUID, limit: int = 50) -> list[ChatMessage]:
        await self.get_chat(chat_id)
        return await self.repo.list_messages(chat_id, limit=limit)

    async def clear_history(self, chat_id: UUID) -> None:
        await self.get_chat(chat_id)
        await self.repo.soft_delete_messages(chat_id)
