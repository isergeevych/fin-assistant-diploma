"""
Описания tools в формате JSON Schema (OpenAI Chat Completions, strict mode)
и валидация аргументов, пришедших от модели.

description — это промпт, который модель читает на каждом запросе. Тексты
лежат в app/prompts/tools/<name>.md и подтягиваются в именованные константы.

Аргументы модели — недоверенные данные. Проверка в два слоя:
1) jsonschema — структура и типы ровно по той схеме, что ушла модели;
2) Pydantic — смысл: даты парсятся, период не перевёрнут и не бесконечен,
   дата курса не из будущего.
"""

import datetime as dt
from datetime import date, timedelta

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.finance.models import CATEGORIES, Category
from app.prompts.loader import load_tool_description

GET_SPENDING_SUMMARY_DESCRIPTION = load_tool_description("get_spending_summary")
GET_EXCHANGE_RATE_DESCRIPTION = load_tool_description("get_exchange_rate")

GET_SPENDING_SUMMARY_PARAMETERS = {
    "type": "object",
    "properties": {
        "period_start": {"type": "string", "description": "Первый день периода, YYYY-MM-DD"},
        "period_end": {"type": "string", "description": "Последний день периода включительно, YYYY-MM-DD"},
        "category": {
            "anyOf": [{"type": "string", "enum": list(CATEGORIES)}, {"type": "null"}],
            "description": "Категория расходов или null — все категории",
        },
    },
    "required": ["period_start", "period_end", "category"],
    "additionalProperties": False,
}

GET_EXCHANGE_RATE_PARAMETERS = {
    "type": "object",
    "properties": {
        "currency": {"type": "string", "description": "Код валюты ISO 4217, например USD или EUR"},
        "date": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Дата курса YYYY-MM-DD или null — текущий курс",
        },
    },
    "required": ["currency", "date"],
    "additionalProperties": False,
}


def _tool(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters, "strict": True},
    }


TOOLS: list[dict] = [
    _tool("get_spending_summary", GET_SPENDING_SUMMARY_DESCRIPTION, GET_SPENDING_SUMMARY_PARAMETERS),
    _tool("get_exchange_rate", GET_EXCHANGE_RATE_DESCRIPTION, GET_EXCHANGE_RATE_PARAMETERS),
]


class SpendingSummaryArgs(BaseModel):
    period_start: date
    period_end: date
    category: Category | None = None

    @model_validator(mode="after")
    def check_period(self):
        if self.period_start > self.period_end:
            raise ValueError("period_start позже period_end")
        if self.period_end - self.period_start > timedelta(days=3 * 366):
            raise ValueError("период длиннее 3 лет")
        return self


class ExchangeRateArgs(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # Поле называется date, поэтому тип пишем через модуль: иначе имя поля
    # перекрыло бы класс date в аннотации.
    date: dt.date | None = None

    @model_validator(mode="after")
    def check_date(self):
        if self.date is not None and self.date > dt.date.today():
            raise ValueError("курс на будущую дату ЦБ ещё не установил")
        return self


ARGS_MODELS: dict[str, type[BaseModel]] = {
    "get_spending_summary": SpendingSummaryArgs,
    "get_exchange_rate": ExchangeRateArgs,
}
_VALIDATORS = {t["function"]["name"]: Draft202012Validator(t["function"]["parameters"]) for t in TOOLS}


class ToolArgsError(ValueError):
    pass


def validate_tool_args(name: str, args: dict) -> BaseModel:
    if name not in ARGS_MODELS:
        raise ToolArgsError(f"неизвестный инструмент {name!r}")
    errors = sorted(_VALIDATORS[name].iter_errors(args), key=lambda e: e.path)
    if errors:
        raise ToolArgsError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors))
    try:
        return ARGS_MODELS[name].model_validate(args)
    except ValidationError as e:
        raise ToolArgsError(str(e)) from e
