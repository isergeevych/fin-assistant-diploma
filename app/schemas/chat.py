"""Схемы запросов и ответов /chat. Единая модель ответа, не привязанная к структуре SDK."""

from typing import Literal

from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.models import ALLOWED_MODEL_IDS

MAX_MESSAGE_CHARS = 8000
MAX_MESSAGES = 50


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"messages": [{"role": "user", "content": "Как начать вести личный бюджет?"}]},
                {
                    "messages": [
                        {"role": "system", "content": "Ты финансовый помощник. Отвечай кратко."},
                        {"role": "user", "content": "Что такое финансовая подушка безопасности?"},
                    ],
                    "model": "openai/gpt-4o-mini",
                    "temperature": 0.3,
                    "max_tokens": 300,
                    "session_id": "demo-session-1",
                },
            ]
        }
    )

    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    model: str | None = Field(default=None, description="ID модели из GET /models; по умолчанию — primary")
    # None = не передавать: gpt-5.4-mini (reasoning-модель) temperature не поддерживает.
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=16000)
    user_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("model")
    @classmethod
    def model_must_be_allowed(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_MODEL_IDS:
            raise ValueError(f"модель не из списка GET /models: {sorted(ALLOWED_MODEL_IDS)}")
        return v

    def llm_params(self) -> dict:
        params: dict = {"max_tokens": self.max_tokens}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        return params


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    content: str
    model: str
    usage: Usage
    finish_reason: str | None
    cached: bool = False

    @classmethod
    def from_openai(cls, response: ChatCompletion) -> "ChatResponse":
        choice = response.choices[0]
        usage = response.usage
        return cls(
            content=choice.message.content or "",
            model=response.model,
            usage=Usage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
            finish_reason=choice.finish_reason,
        )


class ChatDelta(BaseModel):
    """Один кадр стрима: либо кусок текста, либо финальный usage."""

    content: str | None = None
    usage: Usage | None = None


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
