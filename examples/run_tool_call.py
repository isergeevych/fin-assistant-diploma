"""
Б3.1 — прогон цикла tool_call на трёх контрольных запросах (+ один на цепочку tools).

  (а) точно нужен tool        — вопрос о тратах пользователя;
  (б) tool точно не нужен      — общий вопрос о финансах;
  (в) пограничный случай       — «примерно» сколько стоит валюта: можно ответить
                                  по памяти, а можно сходить за официальным курсом;
  (г) цепочка tools (бонус)    — траты в отпуске → курс евро → пересчёт.

Запуск (из корня репозитория):
    python -m examples.run_tool_call
Лог шагов: logs/tool_calls.jsonl
"""

import asyncio
import json

from app.core.config import get_settings
from app.core.logging import setup_json_logging
from app.llm.client import run_tool_call

OWNER = "tg:100001"  # демо-пользователь из синтетических данных

CASES = [
    ("а", "нужен tool", "Сколько я потратил на кафе в сентябре 2026 года?"),
    ("б", "tool не нужен", "Что такое финансовая подушка безопасности и какого она должна быть размера?"),
    ("в", "пограничный", "Сколько примерно стоят 100 евро в рублях?"),
    ("г", "цепочка tools", "Сколько я потратил на путешествия в июле 2026 и сколько это в евро по сегодняшнему курсу?"),
]


async def main() -> None:
    settings = get_settings()
    setup_json_logging(settings.log_dir, "tool_calls.jsonl")
    summary = []
    for case_id, kind, question in CASES:
        print(f"\n===== ({case_id}) {kind}: {question}")
        result = await run_tool_call(question, OWNER, settings=settings)
        for call in result.tool_calls:
            print(f"  tool: {call['name']}({call['args']})")
            print(f"     → {json.dumps(call['result'], ensure_ascii=False)[:300]}")
        print(f"  ответ: {result.answer}")
        print(f"  раундов: {result.rounds}, total_tokens: {result.total_tokens}")
        summary.append({"case": case_id, "kind": kind, "question": question, "tools": result.tool_calls,
                        "answer": result.answer, "total_tokens": result.total_tokens})
    with open(settings.log_dir / "tool_call_cases.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
