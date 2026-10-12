"""Доменная модель финансовых операций. Только Pydantic и stdlib — без инфраструктуры."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Category = Literal[
    "продукты", "кафе", "транспорт", "жильё", "связь", "здоровье",
    "развлечения", "одежда", "путешествия", "зарплата", "переводы", "прочее",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]


class Transaction(BaseModel):
    id: str
    owner_external_id: str
    occurred_at: date
    amount_rub: Decimal  # < 0 — расход, > 0 — доход
    currency: str = "RUB"  # валюта исходной операции
    amount_original: Decimal  # сумма в исходной валюте
    category: Category
    merchant: str
