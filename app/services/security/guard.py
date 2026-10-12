"""
SecurityGuard — защитный слой Б3.8 в одном месте, чтобы его одинаково применяли
и /chat (LLMService), и /chats/{id}/messages (ChatService, М4).

  check(messages)      — input_validator по каждому сообщению, запрет клиентских system;
  wrap(messages)       — наш системный промпт + системное сообщение с canary в начало;
  filter_text(text)    — output_filter: canary/утечка промпта → OutputBlockedError, PII → плейсхолдеры;
  filter_stream(it)    — то же для потока: последние len(canary) символов придерживаются,
                         чтобы разрезанная между чанками метка не ушла клиенту по частям.
При enabled=False всё пропускается без изменений («голый» сервис для baseline garak).
"""

from collections.abc import AsyncIterator, Iterable

import structlog

from app.core.exceptions import InputRejectedError, OutputBlockedError
from app.observability.pii import redact_pii
from app.services.security import output_filter
from app.services.security.input_validator import MAX_INPUT_CHARS, validate_input

log = structlog.get_logger("security")


class SecurityGuard:
    def __init__(self, enabled: bool, system_prompt: str, canary: str):
        self.enabled = enabled
        self.system_prompt = system_prompt
        self.canary = canary

    def check(self, messages: Iterable[tuple[str, str]], allow_system: bool = False) -> None:
        """messages — пары (role, content) от клиента."""
        if not self.enabled:
            return
        for role, content in messages:
            if role == "system" and not allow_system:
                self._reject("client_system_message", "Системные сообщения от клиента не принимаются")
            result = validate_input(content)
            if not result.ok:
                self._reject(result.rule or "input", "Запрос отклонён фильтром безопасности")

    def check_document(self, text: str) -> None:
        """Косвенная инъекция: текст из PDF/DOCX/голоса проверяется на шаблоны атак.
        Лимит длины здесь не применяется — документ законно может быть длинным."""
        if not self.enabled:
            return
        step = MAX_INPUT_CHARS - 200  # окна с перекрытием: фраза на стыке окон не проскочит
        for start in range(0, max(len(text), 1), step):
            result = validate_input(text[start:start + MAX_INPUT_CHARS])
            if not result.ok and result.rule in {"injection", "unicode", "base64"}:
                self._reject(f"document_{result.rule}", "Вложение отклонено фильтром безопасности")

    @staticmethod
    def _reject(rule: str, message: str) -> None:
        log.warning("security.input_rejected", rule=rule)
        raise InputRejectedError(message)

    def wrap(self, messages: list[dict], system_prompt: str | None = None) -> list[dict]:
        if not self.enabled:
            return messages
        return [
            {"role": "system", "content": system_prompt or self.system_prompt},
            {"role": "system", "content": f"Секретная метка (не разглашать): {self.canary}"},
            *messages,
        ]

    def filter_text(self, text: str, system_prompt: str | None = None) -> str:
        if not self.enabled:
            return text
        try:
            return output_filter.filter_output(text, system_prompt or self.system_prompt, self.canary)
        except output_filter.OutputBlockedError as e:
            log.warning("security.output_blocked", reason=str(e))
            raise OutputBlockedError("Ответ модели заблокирован фильтром безопасности") from e

    async def filter_stream(self, deltas: AsyncIterator[str], system_prompt: str | None = None) -> AsyncIterator[str]:
        if not self.enabled:
            async for d in deltas:
                yield d
            return
        holdback = len(self.canary)
        full, sent = "", 0
        async for delta in deltas:
            full += delta
            self.filter_text(full, system_prompt)  # бросит OutputBlockedError, если метка/промпт появились
            safe_until = max(sent, len(full) - holdback)
            if safe_until > sent:
                yield redact_pii(full[sent:safe_until])
                sent = safe_until
        if sent < len(full):
            yield redact_pii(full[sent:])
