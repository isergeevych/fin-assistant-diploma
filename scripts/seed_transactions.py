"""
Генератор СИНТЕТИЧЕСКИХ операций (решение D-07: реальные выписки в проект не грузим).

Фиксированный seed → одинаковые данные при каждом запуске, поэтому тесты и
golden-датасеты воспроизводимы. Два демо-пользователя, апрель–сентябрь 2026.

Запуск:
    python scripts/seed_transactions.py
"""

import csv
import random
import uuid
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "finance" / "transactions.csv"
SEED = 42
USERS = {"tg:100001": 1.0, "tg:100002": 1.6}  # множитель уровня расходов
MONTHS = [(2026, m) for m in range(4, 10)]
NAMESPACE = uuid.UUID("7f3c2a10-0000-4000-8000-000000000001")

# категория: (мерчанты, мин, макс, операций в месяц)
SPENDING = {
    "продукты": (["Пятёрочка", "Перекрёсток", "ВкусВилл", "Магнит"], 300, 3500, 14),
    "кафе": (["Шоколадница", "Кофемания", "Теремок", "Додо Пицца"], 250, 2500, 7),
    "транспорт": (["Метро", "Яндекс Go", "Ситидрайв"], 60, 1200, 18),
    "жильё": (["Аренда квартиры", "ЖКУ Мосэнергосбыт"], 3000, 45000, 2),
    "связь": (["МТС", "Ростелеком"], 450, 900, 2),
    "здоровье": (["Аптека Ригла", "Клиника Медси"], 300, 6000, 2),
    "развлечения": (["Кинопоиск", "Каро Фильм", "Steam"], 299, 2500, 3),
    "одежда": (["Lamoda", "Wildberries"], 900, 7000, 2),
}
SALARY = 120_000


def main() -> None:
    rng = random.Random(SEED)
    rows = []
    for owner, factor in USERS.items():
        for year, month in MONTHS:
            first = date(year, month, 1)
            days = ((first.replace(month=month % 12 + 1, year=year + month // 12)) - first).days

            def add(day: int, amount: float, category: str, merchant: str, currency: str = "RUB", original=None):
                occurred = first + timedelta(days=min(day, days) - 1)
                idx = len(rows)
                rows.append({
                    "id": str(uuid.uuid5(NAMESPACE, f"{owner}:{idx}")),
                    "owner_external_id": owner,
                    "occurred_at": occurred.isoformat(),
                    "amount_rub": f"{amount:.2f}",
                    "currency": currency,
                    "amount_original": f"{(original if original is not None else amount):.2f}",
                    "category": category,
                    "merchant": merchant,
                })

            add(5, SALARY * factor, "зарплата", "ООО «Ромашка»")
            add(20, SALARY * factor * 0.6, "зарплата", "ООО «Ромашка» (аванс)")
            for category, (merchants, lo, hi, per_month) in SPENDING.items():
                for _ in range(per_month):
                    add(rng.randint(1, days), -round(rng.uniform(lo, hi) * factor, 2), category, rng.choice(merchants))
            if (year, month) == (2026, 7):  # отпуск в июле — траты в евро
                for _ in range(6):
                    eur = round(rng.uniform(15, 180), 2)
                    add(rng.randint(10, 20), -round(eur * 93.4, 2), "путешествия", "Booking/Cafe Barcelona", "EUR", -eur)

    rows.sort(key=lambda r: (r["owner_external_id"], r["occurred_at"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Записано {len(rows)} операций в {OUT}")


if __name__ == "__main__":
    main()
