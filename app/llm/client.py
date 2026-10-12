"""
Полный цикл Function Calling (Б3.1):

  messages + tools → ответ модели
    ├─ нет tool_calls → это финальный текст
    └─ есть tool_calls → в историю добавляется ассистентский ход С tool_calls
                         → каждый tool выполняется → role:"tool" с tool_call_id
                         → повторный запрос → … → финальный текст

Без ассистентского хода с tool_calls второй запрос отвалится: API требует,
чтобы каждому role:"tool" предшествовал вызов с тем же id.
Число раундов ограничено (MAX_TOOL_ROUNDS) — защита от зацикливания.

System prompt подгружается из app/prompts/system_v1.j2 — inline-строки здесь нет.
Каждый шаг пишется JSON-строкой в logs/: input → tool + args → result → answer → tokens.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date

from openai import AsyncOpenAI

from app.core.config import Settings, get_settings
from app.prompts.loader import render_system_prompt
from app.tools.handlers import ToolContext, execute_tool
from app.tools.schemas import TOOLS

MAX_TOOL_ROUNDS = 4
log = logging.getLogger("llm.tool_call")


@dataclass
class ToolCallResult:
    answer: str
    tool_calls: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    rounds: int = 0
    request_id: str = ""


def build_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.llm.api_key.get_secret_value(),
        base_url=settings.llm.base_url,
        timeout=settings.llm.request_timeout,
    )


async def run_tool_call(
    user_input: str,
    owner_external_id: str,
    client: AsyncOpenAI | None = None,
    settings: Settings | None = None,
    model: str | None = None,
) -> ToolCallResult:
    settings = settings or get_settings()
    client = client or build_client(settings)
    model = model or settings.llm.default_model
    ctx = ToolContext(owner_external_id=owner_external_id, settings=settings)
    request_id = uuid.uuid4().hex[:12]
    result = ToolCallResult(answer="", request_id=request_id)

    system_prompt = render_system_prompt(
        settings.prompt_version, product_name=settings.product_name, today=date.today().isoformat()
    )
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]
    _log("tool_call.input", request_id, model=model, user_input=user_input, owner=owner_external_id)

    for round_no in range(1, MAX_TOOL_ROUNDS + 2):
        started = time.perf_counter()
        response = await client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS, tool_choice="auto"
        )
        result.rounds = round_no
        if response.usage:
            result.total_tokens += response.usage.total_tokens
        message = response.choices[0].message
        _log("tool_call.llm_response", request_id, round=round_no,
             has_tool_calls=bool(message.tool_calls), latency_ms=round((time.perf_counter() - started) * 1000),
             total_tokens=response.usage.total_tokens if response.usage else None)

        if not message.tool_calls:
            result.answer = message.content or ""
            break

        if round_no > MAX_TOOL_ROUNDS:  # модель всё ещё просит tools — останавливаемся
            result.answer = "Не удалось завершить запрос: слишком много шагов. Попробуйте переформулировать."
            _log("tool_call.max_rounds", request_id, rounds=round_no)
            break

        # Ассистентский ход с tool_calls — обязательно до role:"tool".
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [tc.model_dump(include={"id", "type", "function"}) for tc in message.tool_calls],
        })
        for tc in message.tool_calls:
            _log("tool_call.tool_selected", request_id, tool=tc.function.name, args=tc.function.arguments)
            tool_result = await execute_tool(tc.function.name, tc.function.arguments, ctx)
            _log("tool_call.tool_result", request_id, tool=tc.function.name, result=tool_result)
            result.tool_calls.append({"name": tc.function.name, "args": tc.function.arguments, "result": tool_result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })

    _log("tool_call.final", request_id, answer=result.answer, total_tokens=result.total_tokens,
         rounds=result.rounds, tools_used=[c["name"] for c in result.tool_calls])
    return result


def _log(event: str, request_id: str, **fields) -> None:
    log.info(event, extra={"fields": {"request_id": request_id, **fields}})
