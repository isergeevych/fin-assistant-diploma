"""
Статический каталог моделей для GET /models и allowlist для поля model в ChatRequest.

Цены — $ за 1M токенов, сверены с openrouter.ai/api/v1/models на 2026-10-12.
Allowlist нужен для безопасности и бюджета: без него клиент мог бы
попросить через наш ключ любую, в том числе самую дорогую модель.
"""

from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    vendor: str
    context_window: int
    input_price_per_1m: float
    output_price_per_1m: float
    supports_temperature: bool
    role: str


MODELS: list[ModelInfo] = [
    ModelInfo(id="openai/gpt-5.4-mini", vendor="OpenAI", context_window=400_000,
              input_price_per_1m=0.75, output_price_per_1m=4.50, supports_temperature=False,
              role="primary: диалог, tool calling"),
    ModelInfo(id="anthropic/claude-haiku-4.5", vendor="Anthropic", context_window=200_000,
              input_price_per_1m=1.00, output_price_per_1m=5.00, supports_temperature=True,
              role="fallback другого вендора"),
    ModelInfo(id="openai/gpt-4o-mini", vendor="OpenAI", context_window=128_000,
              input_price_per_1m=0.15, output_price_per_1m=0.60, supports_temperature=True,
              role="дешёвая модель для экспериментов"),
    ModelInfo(id="deepseek/deepseek-chat", vendor="DeepSeek", context_window=163_840,
              input_price_per_1m=0.32, output_price_per_1m=0.89, supports_temperature=True,
              role="дешёвая альтернатива"),
]

ALLOWED_MODEL_IDS: frozenset[str] = frozenset(m.id for m in MODELS)
