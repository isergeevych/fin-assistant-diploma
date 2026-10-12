"""
Обработчики tools — обычный Python, делающий реальную работу:

- get_spending_summary — читает операции пользователя из репозитория (CSV);
- get_exchange_rate    — ходит в публичный API ЦБ РФ (XML_daily, без ключа).

Диспетчеризация — только через явный словарь HANDLERS (allowlist): никаких
getattr/eval по имени, которое прислала модель.

Владелец данных берётся из ToolContext, который заполняет сервер. В схеме
tool нет параметра user_id — модель не может запросить чужие операции.
"""

import asyncio
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx

from app.core.config import Settings
from app.finance.repository import CsvFinanceRepository, FinanceRepository
from app.tools.schemas import ExchangeRateArgs, SpendingSummaryArgs, ToolArgsError, validate_tool_args


@dataclass
class ToolContext:
    owner_external_id: str
    settings: Settings
    repository: FinanceRepository | None = None
    http_transport: httpx.AsyncBaseTransport | None = None  # подмена сети в тестах

    def repo(self) -> FinanceRepository:
        return self.repository or CsvFinanceRepository(self.settings.transactions_csv)


async def get_spending_summary(args: SpendingSummaryArgs, ctx: ToolContext) -> dict:
    # CSV читается синхронно — уводим в поток, чтобы не блокировать event loop.
    txs = await asyncio.to_thread(
        ctx.repo().list_transactions, ctx.owner_external_id, args.period_start, args.period_end, args.category
    )
    expenses = [t for t in txs if t.amount_rub < 0]
    incomes = [t for t in txs if t.amount_rub > 0]
    by_category: dict[str, Decimal] = defaultdict(Decimal)
    by_merchant: dict[str, Decimal] = defaultdict(Decimal)
    for t in expenses:
        by_category[t.category] += -t.amount_rub
        by_merchant[t.merchant] += -t.amount_rub
    top_merchants = sorted(by_merchant.items(), key=lambda kv: kv[1], reverse=True)[:3]
    foreign = sorted({t.currency for t in txs if t.currency != "RUB"})
    return {
        "period": {"start": args.period_start.isoformat(), "end": args.period_end.isoformat()},
        "category": args.category,
        "transactions_count": len(txs),
        "total_expense_rub": float(sum((-t.amount_rub for t in expenses), Decimal(0))),
        "total_income_rub": float(sum((t.amount_rub for t in incomes), Decimal(0))),
        "expense_by_category_rub": {k: float(v) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
        "top_merchants_rub": {k: float(v) for k, v in top_merchants},
        "foreign_currencies_in_period": foreign,
    }


async def get_exchange_rate(args: ExchangeRateArgs, ctx: ToolContext) -> dict:
    params = {"date_req": args.date.strftime("%d/%m/%Y")} if args.date else {}
    async with httpx.AsyncClient(timeout=10, transport=ctx.http_transport) as http:
        response = await http.get(ctx.settings.cbr_daily_url, params=params)
        response.raise_for_status()
    # Ответ ЦБ — XML в windows-1251; ElementTree читает кодировку из пролога.
    root = ET.fromstring(response.content)
    for valute in root.iter("Valute"):
        if valute.findtext("CharCode") == args.currency:
            nominal = int(valute.findtext("Nominal"))
            value = Decimal(valute.findtext("Value").replace(",", "."))
            return {
                "currency": args.currency,
                "name": valute.findtext("Name"),
                "rub_per_unit": float(round(value / nominal, 4)),
                "rate_date": _cbr_date(root.get("Date")),
                "source": "ЦБ РФ",
            }
    return {"error": f"ЦБ РФ не публикует курс валюты {args.currency}"}


def _cbr_date(value: str | None) -> str | None:
    if not value:
        return None
    day, month, year = value.split(".")
    return date(int(year), int(month), int(day)).isoformat()


HANDLERS = {
    "get_spending_summary": get_spending_summary,
    "get_exchange_rate": get_exchange_rate,
}


async def execute_tool(name: str, raw_arguments: str, ctx: ToolContext) -> dict:
    """Никогда не бросает исключение в цикл: ошибка возвращается модели как результат,
    чтобы она могла исправить аргументы или честно сказать пользователю."""
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"неизвестный инструмент {name!r}"}
    try:
        args = validate_tool_args(name, json.loads(raw_arguments or "{}"))
    except (json.JSONDecodeError, ToolArgsError) as e:
        return {"error": f"некорректные аргументы: {e}"}
    try:
        return await handler(args, ctx)
    except httpx.HTTPError as e:
        return {"error": f"внешний сервис недоступен: {type(e).__name__}"}
    except FileNotFoundError as e:
        return {"error": str(e)}
