"""
Доступ к операциям пользователя.

FinanceRepository — контракт (Protocol); сейчас реализация читает CSV,
в модуле 4 появится Postgres-реализация с тем же интерфейсом.

Безопасность: владелец (owner_external_id) — обязательный аргумент каждого
метода, и передаёт его сервер из контекста запроса, а не модель. Так модель
физически не может запросить операции чужого пользователя.
"""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from app.finance.models import Transaction


class FinanceRepository(Protocol):
    def list_transactions(
        self, owner_external_id: str, start: date, end: date, category: str | None = None
    ) -> list[Transaction]: ...


class CsvFinanceRepository:
    def __init__(self, path: Path):
        self.path = path

    def list_transactions(
        self, owner_external_id: str, start: date, end: date, category: str | None = None
    ) -> list[Transaction]:
        if not self.path.exists():
            raise FileNotFoundError(f"Нет файла операций {self.path}: запустите scripts/seed_transactions.py")
        result = []
        with self.path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["owner_external_id"] != owner_external_id:
                    continue
                tx = Transaction(
                    id=row["id"],
                    owner_external_id=row["owner_external_id"],
                    occurred_at=date.fromisoformat(row["occurred_at"]),
                    amount_rub=Decimal(row["amount_rub"]),
                    currency=row["currency"],
                    amount_original=Decimal(row["amount_original"]),
                    category=row["category"],
                    merchant=row["merchant"],
                )
                if start <= tx.occurred_at <= end and (category is None or tx.category == category):
                    result.append(tx)
        return result
