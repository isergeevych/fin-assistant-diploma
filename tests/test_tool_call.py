"""
Б3.1 — тесты цикла tool_call и инструментов. Без сети и без ключей:
LLM подменяется фейковым клиентом, ЦБ РФ — httpx.MockTransport.
"""

import csv
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from openai.types.chat import ChatCompletion

from app.core.config import LLMSettings, Settings
from app.llm.client import run_tool_call
from app.tools.handlers import ToolContext, execute_tool
from app.tools.schemas import TOOLS, ToolArgsError, validate_tool_args

CBR_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<ValCurs Date="10.10.2026" name="Foreign Currency Market">'
    '<Valute ID="R01239"><NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal>'
    "<Name>Euro</Name><Value>93,4000</Value></Valute>"
    '<Valute ID="R01375"><NumCode>156</NumCode><CharCode>CNY</CharCode><Nominal>10</Nominal>'
    "<Name>Yuan</Name><Value>112,5000</Value></Valute>"
    "</ValCurs>"
).encode("cp1251")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    path = tmp_path / "tx.csv"
    rows = [
        ("1", "tg:A", "2026-09-03", "-500.00", "кафе", "Теремок"),
        ("2", "tg:A", "2026-09-10", "-1500.00", "кафе", "Кофемания"),
        ("3", "tg:A", "2026-09-12", "-3000.00", "продукты", "ВкусВилл"),
        ("4", "tg:A", "2026-09-05", "100000.00", "зарплата", "ООО"),
        ("5", "tg:A", "2026-08-30", "-999.00", "кафе", "Теремок"),  # вне периода
        ("6", "tg:B", "2026-09-04", "-77777.00", "кафе", "Чужое кафе"),  # другой пользователь
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "owner_external_id", "occurred_at", "amount_rub", "currency", "amount_original", "category", "merchant"])
        for id_, owner, day, amount, cat, merchant in rows:
            w.writerow([id_, owner, day, amount, "RUB", amount, cat, merchant])
    return Settings(llm=LLMSettings(api_key="test-key"), transactions_csv=path, log_dir=tmp_path)


# ---------- схемы и валидация ----------

def test_tool_schemas_are_valid_json_schema():
    for tool in TOOLS:
        Draft202012Validator.check_schema(tool["function"]["parameters"])
        assert tool["function"]["strict"] is True
        assert tool["function"]["parameters"]["additionalProperties"] is False
        assert len(tool["function"]["description"]) > 100  # описание подтянуто из файла, а не пустое


def test_validate_rejects_bad_args():
    with pytest.raises(ToolArgsError):  # перевёрнутый период
        validate_tool_args("get_spending_summary", {"period_start": "2026-09-30", "period_end": "2026-09-01", "category": None})
    with pytest.raises(ToolArgsError):  # категории нет в enum
        validate_tool_args("get_spending_summary", {"period_start": "2026-09-01", "period_end": "2026-09-30", "category": "казино"})
    with pytest.raises(ToolArgsError):  # лишнее поле — попытка подсунуть владельца
        validate_tool_args("get_spending_summary", {"period_start": "2026-09-01", "period_end": "2026-09-30",
                                                    "category": None, "owner_external_id": "tg:B"})
    with pytest.raises(ToolArgsError):
        validate_tool_args("get_exchange_rate", {"currency": "euro", "date": None})


# ---------- обработчики ----------

async def test_spending_summary_counts_only_owner_and_period(settings):
    ctx = ToolContext(owner_external_id="tg:A", settings=settings)
    result = await execute_tool(
        "get_spending_summary",
        json.dumps({"period_start": "2026-09-01", "period_end": "2026-09-30", "category": "кафе"}),
        ctx,
    )
    assert result["total_expense_rub"] == 2000.0  # 500 + 1500; чужие 77 777 и августовские 999 не попали
    assert result["transactions_count"] == 2
    assert "Чужое кафе" not in result["top_merchants_rub"]


async def test_exchange_rate_parses_cbr_xml_with_nominal(settings):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=CBR_XML))
    ctx = ToolContext(owner_external_id="tg:A", settings=settings, http_transport=transport)
    eur = await execute_tool("get_exchange_rate", '{"currency": "EUR", "date": null}', ctx)
    cny = await execute_tool("get_exchange_rate", '{"currency": "CNY", "date": "2026-10-10"}', ctx)
    assert eur["rub_per_unit"] == 93.4 and eur["rate_date"] == "2026-10-10"
    assert cny["rub_per_unit"] == 11.25  # номинал 10 юаней → курс за 1 юань


async def test_exchange_rate_falls_back_to_mirror_when_cbr_unreachable(settings):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host + request.url.path)
        if request.url.host == "www.cbr.ru":
            raise httpx.ConnectTimeout("cbr.ru не отвечает с зарубежного IP", request=request)
        return httpx.Response(200, content=CBR_XML)

    ctx = ToolContext(owner_external_id="tg:A", settings=settings, http_transport=httpx.MockTransport(handler))
    eur = await execute_tool("get_exchange_rate", '{"currency": "EUR", "date": "2026-10-09"}', ctx)
    assert eur["rub_per_unit"] == 93.4 and "зеркало" in eur["source"]
    assert seen[-1] == "www.cbr-xml-daily.ru/archive/2026/10/09/daily.xml"  # дата → архив зеркала


async def test_exchange_rate_both_sources_down_is_error_for_model(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("сети нет", request=request)

    ctx = ToolContext(owner_external_id="tg:A", settings=settings, http_transport=httpx.MockTransport(handler))
    result = await execute_tool("get_exchange_rate", '{"currency": "EUR", "date": null}', ctx)
    assert result == {"error": "внешний сервис недоступен: ConnectError"}


async def test_unknown_tool_and_bad_json_do_not_raise(settings):
    ctx = ToolContext(owner_external_id="tg:A", settings=settings)
    assert "error" in await execute_tool("delete_all_data", "{}", ctx)
    assert "error" in await execute_tool("get_exchange_rate", "{not json", ctx)


# ---------- цикл tool_call с фейковым LLM ----------

def _completion(message: dict, total_tokens: int = 100) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "fake",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": total_tokens - 10, "completion_tokens": 10, "total_tokens": total_tokens},
    })


class FakeLLM:
    def __init__(self, responses: list[ChatCompletion]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


async def test_full_cycle_with_tool_call(settings):
    tool_call = {"id": "call_1", "type": "function", "function": {
        "name": "get_spending_summary",
        "arguments": '{"period_start": "2026-09-01", "period_end": "2026-09-30", "category": "кафе"}'}}
    fake = FakeLLM([
        _completion({"content": None, "tool_calls": [tool_call]}, 300),
        _completion({"content": "В сентябре на кафе ушло 2 000 ₽."}, 400),
    ])
    result = await run_tool_call("Сколько я потратил на кафе в сентябре?", "tg:A", client=fake, settings=settings)

    assert result.answer == "В сентябре на кафе ушло 2 000 ₽."
    assert result.total_tokens == 700 and result.rounds == 2
    second_messages = fake.calls[1]["messages"]
    # ассистентский ход с tool_calls стоит ПЕРЕД role:"tool" с тем же id
    assert second_messages[-2]["role"] == "assistant" and second_messages[-2]["tool_calls"][0]["id"] == "call_1"
    assert second_messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": second_messages[-1]["content"]}
    assert json.loads(second_messages[-1]["content"])["total_expense_rub"] == 2000.0
    # system prompt пришёл из файла-шаблона
    assert "Финпомощник" in fake.calls[0]["messages"][0]["content"]


async def test_no_tool_call_returns_text_directly(settings):
    fake = FakeLLM([_completion({"content": "Подушка — запас на 3–6 месяцев расходов."})])
    result = await run_tool_call("Что такое подушка безопасности?", "tg:A", client=fake, settings=settings)
    assert result.tool_calls == [] and result.rounds == 1
    assert result.answer.startswith("Подушка")
