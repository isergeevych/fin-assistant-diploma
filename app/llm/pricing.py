"""Стоимость вызова по usage и ценам из каталога моделей (app/schemas/models.py)."""

from app.schemas.models import MODELS

_PRICES = {m.id: (m.input_price_per_1m, m.output_price_per_1m) for m in MODELS}


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """$ за вызов. Неизвестная модель (например локальная) — 0.0."""
    price_in, price_out = _PRICES.get(model, (0.0, 0.0))
    return round((prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000, 8)
