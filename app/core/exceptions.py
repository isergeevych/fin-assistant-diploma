"""
Доменные исключения LLM-слоя. Сервис и HTTP-слой не знают про классы
ошибок конкретного SDK: они видят только эти исключения, а маппинг
в HTTP-коды делается в одном месте (app/main.py).
"""


class LLMError(Exception):
    code = "llm_error"
    http_status = 502

    def __init__(self, message: str = "Ошибка LLM-провайдера"):
        super().__init__(message)
        self.message = message


class LLMRateLimitError(LLMError):
    code = "llm_rate_limit"
    http_status = 429


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    http_status = 504


class LLMAuthError(LLMError):
    code = "llm_auth"
    http_status = 502


class LLMUnavailableError(LLMError):
    """Все провайдеры цепочки недоступны (или их circuit breaker открыт)."""

    code = "llm_unavailable"
    http_status = 502
