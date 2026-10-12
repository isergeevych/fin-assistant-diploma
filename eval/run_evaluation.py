"""
Б3.7 — прогон golden dataset с LLM-as-judge (G-Eval: reason-then-score).

Это не pytest-тест, а отдельный CLI: медленный и платный, запускается руками
при смене промпта / модели (быстрые unit-тесты — в tests/).

  python eval/run_evaluation.py --golden eval/golden_dataset.json \
      --judge anthropic/claude-sonnet-4.6 --out eval/runs/2026-10-12.json

Для каждого item:
  1. ответ приложения — прямой вызов сервиса run_tool_call (тот же system prompt
     и tools, что в проде), детерминизм — seed (gpt-5.4-mini не принимает
     temperature, решение D-10 в плане);
  2. судья — модель сильнее production, temperature=0, response_format=json_object,
     промпт app/prompts/judge_geval.j2 требует reasoning ДО оценок;
  3. дополнительно — детерминированные проверки expected_keywords / must_not_contain.
Результат: eval/runs/<дата>.json (структура из задания) — читается jq.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai import AsyncOpenAI  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.llm.client import run_tool_call  # noqa: E402
from app.llm.parsing import parse_json_response  # noqa: E402
from app.prompts.loader import render_template  # noqa: E402

CRITERIA = ("relevance", "correctness", "completeness")
JUDGE_REASONING_EFFORT = "low"  # Gemini не даёт выключить reasoning; low — дешевле и быстрее


class Verdict(BaseModel):
    reasoning: str
    relevance: int = Field(ge=1, le=5)
    correctness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    explanation: str = ""


async def judge(client: AsyncOpenAI, model: str, item: dict, answer: str) -> Verdict:
    prompt = render_template("judge_geval.j2", question=item["question"],
                             expected_answer=item["expected_answer"], answer=answer)
    last_error: Exception | None = None
    for _ in range(2):  # одна повторная попытка, если судья вернул невалидный JSON
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
            # у reasoning-моделей (Gemini Pro) «размышления» входят в лимит: 1200 не хватало бы на сам JSON
            max_tokens=4000,
            extra_body={"reasoning_effort": JUDGE_REASONING_EFFORT},
        )
        message = response.choices[0].message
        if message.refusal:  # повтор не поможет: фильтр провайдера детерминирован
            raise RuntimeError(f"судья отказал на {item['id']}: {message.refusal[:200]}")
        try:
            return Verdict.model_validate(parse_json_response(response.choices[0].message.content or ""))
        except ValueError as e:
            last_error = e
    raise RuntimeError(f"судья не вернул валидный JSON для {item['id']}: {last_error}")


def keyword_checks(item: dict, answer: str) -> dict:
    norm = answer.lower().replace(" ", " ").replace(" ", " ")
    return {
        "keywords_found": [k for k in item.get("expected_keywords", []) if k.lower() in norm],
        "must_not_violations": [k for k in item.get("must_not_contain", []) if k.lower() in norm],
    }


async def evaluate_item(item: dict, owner: str, judge_client: AsyncOpenAI, judge_model: str,
                        sem: asyncio.Semaphore) -> dict:
    async with sem:
        started = time.perf_counter()
        result = await run_tool_call(item["question"], owner, llm_params={"seed": 42})
        latency_ms = round((time.perf_counter() - started) * 1000)
        verdict = await judge(judge_client, judge_model, item, result.answer)
    print(f"  {item['id']:<8} c={verdict.correctness} r={verdict.relevance} f={verdict.completeness}  {verdict.explanation[:90]}")
    return {
        "id": item["id"],
        "category": item["category"],
        "difficulty": item["difficulty"],
        "question": item["question"],
        "answer": result.answer,
        "scores": {c: getattr(verdict, c) for c in CRITERIA},
        "reasoning": verdict.reasoning,
        "explanation": verdict.explanation,
        "tools_used": [c["name"] for c in result.tool_calls],
        "total_tokens": result.total_tokens,
        "latency_ms": latency_ms,
        **keyword_checks(item, result.answer),
    }


def aggregates(items: list[dict]) -> dict:
    agg = {f"{c}_avg": round(statistics.mean(i["scores"][c] for i in items), 3) for c in CRITERIA}
    agg["min_correctness"] = min(i["scores"]["correctness"] for i in items)
    agg["must_not_violations"] = sum(len(i["must_not_violations"]) for i in items)
    agg["by_category_correctness_avg"] = {
        cat: round(statistics.mean(i["scores"]["correctness"] for i in items if i["category"] == cat), 3)
        for cat in sorted({i["category"] for i in items})
    }
    return agg


async def main() -> None:
    parser = argparse.ArgumentParser(description="G-Eval прогон golden dataset")
    parser.add_argument("--golden", type=Path, default=ROOT / "eval" / "golden_dataset.json")
    # Claude Sonnet 5.5 отказывал на части items (content_filter: «reverse engineering model outputs» —
    # ложное срабатывание на промпт судьи), поэтому судья — Gemini Pro: другой вендор, сильнее gpt-5.4-mini.
    parser.add_argument("--judge", default="google/gemini-3.1-pro-preview")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None, help="первые N items — для быстрой проверки")
    args = parser.parse_args()

    settings = get_settings()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    items = golden["items"][: args.limit] if args.limit else golden["items"]
    judge_client = AsyncOpenAI(api_key=settings.llm.api_key.get_secret_value(), base_url=settings.llm.base_url,
                               timeout=120, max_retries=3)
    print(f"model_under_test={settings.llm.default_model} judge={args.judge} items={len(items)}")

    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*[
        evaluate_item(item, golden["owner_external_id"], judge_client, args.judge, sem) for item in items
    ])

    out = args.out or ROOT / "eval" / "runs" / f"{date.today().isoformat()}.json"
    if args.out is None and out.exists():  # не затираем утренний прогон вечерним
        out = out.with_name(f"{date.today().isoformat()}_{datetime.now():%H%M}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    run = {
        "run_id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_under_test": settings.llm.default_model,
        "judge_model": args.judge,
        "golden_version": golden["version"],
        "prompt_version": settings.prompt_version,
        "items": results,
        "aggregates": aggregates(results),
    }
    out.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run["aggregates"], ensure_ascii=False, indent=2))
    print(f"→ {out.resolve().relative_to(ROOT) if out.resolve().is_relative_to(ROOT) else out}")


if __name__ == "__main__":
    asyncio.run(main())
