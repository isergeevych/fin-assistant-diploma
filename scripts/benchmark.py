"""
Б3.3 — бенчмарк sync vs async на 20 одинаковых по длине промптах.

Режимы:
  (а) старый синхронный клиент (OpenAI SDK, как в модуле 2) — последовательно;
  (б) AsyncLLMClient.batch_chat при concurrency ∈ {1, 5, 10}.
Плюс эксперимент «всё или ничего»: batch_chat vs batch_chat_strict, когда
3-й запрос идёт на несуществующую модель.

В бенчмарке только primary-провайдер (fallback и local отключены), иначе
сравнение не про конкурентность. Результаты: scripts/benchmark_results.md

Запуск:
    python -m scripts.benchmark
"""

import asyncio
import statistics
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from app.core.config import get_settings
from app.services.llm_client import AsyncLLMClient

CONCEPTS = [
    "сложный процент", "инфляция", "диверсификация", "ликвидность", "кредитная история",
    "ключевая ставка", "налоговый вычет", "финансовая подушка", "кешбэк", "ипотека",
    "страхование вкладов", "ИИС", "облигация", "дивиденды", "бюджет 50/30/20",
    "аннуитетный платёж", "рефинансирование", "овердрафт", "эквайринг", "самозанятость",
]
PROMPTS = [f"Объясни одним абзацем (3–4 предложения) концепцию №{i + 1}: {c}." for i, c in enumerate(CONCEPTS)]
PARAMS = {"max_tokens": 400}
RESULTS = Path(__file__).with_name("benchmark_results.md")


def run_sync(settings) -> float:
    client = OpenAI(api_key=settings.llm.api_key.get_secret_value(), base_url=settings.llm.base_url,
                    timeout=settings.llm.request_timeout)
    started = time.perf_counter()
    for prompt in PROMPTS:
        client.chat.completions.create(model=settings.llm.default_model,
                                       messages=[{"role": "user", "content": prompt}], **PARAMS)
    return time.perf_counter() - started


async def run_async(settings, concurrency: int) -> tuple[float, int]:
    llm_settings = settings.llm.model_copy(update={
        "max_concurrency": concurrency, "fallback_model": None, "local_base_url": None, "call_timeout": 60.0,
    })
    client = AsyncLLMClient(llm_settings)
    started = time.perf_counter()
    results = await client.batch_chat(PROMPTS, concurrency=concurrency, **PARAMS)
    elapsed = time.perf_counter() - started
    await client.aclose()
    return elapsed, sum(isinstance(r, Exception) for r in results)


async def strict_vs_gather(settings) -> list[str]:
    llm_settings = settings.llm.model_copy(update={"max_concurrency": 5, "fallback_model": None, "local_base_url": None})
    client = AsyncLLMClient(llm_settings)
    prompts = PROMPTS[:5]
    models = [None, None, "openai/no-such-model-xyz", None, None]
    lines = []

    # gather: подменяем модель только у 3-го запроса через strict-хелпер по одному
    gathered = await asyncio.gather(*[client._complete_with_model(p, m, **PARAMS) for p, m in zip(prompts, models)],
                                    return_exceptions=True)
    ok = sum(not isinstance(r, Exception) for r in gathered)
    lines.append(f"- `batch_chat` (gather, return_exceptions=True): **{ok} из 5** ответов получены; "
                 f"на позиции 3 — `{type(gathered[2]).__name__}: {gathered[2]}`")

    started = time.perf_counter()
    try:
        await client.batch_chat_strict(prompts, models=models, **PARAMS)
        lines.append("- `batch_chat_strict`: неожиданно без ошибок")
    except* Exception as eg:  # noqa: BLE001
        lines.append(f"- `batch_chat_strict` (TaskGroup): **0 из 5** — `ExceptionGroup` с "
                     f"{len(eg.exceptions)} исключением(ями): `{type(eg.exceptions[0]).__name__}`; остальные задачи "
                     f"отменены через {time.perf_counter() - started:.2f} с")
    await client.aclose()
    return lines


async def main() -> None:
    settings = get_settings()
    print(f"Модель: {settings.llm.default_model}, промптов: {len(PROMPTS)}")
    rows = []
    sync_time = run_sync(settings)
    print(f"sync последовательно: {sync_time:.1f} с")
    rows.append(("sync, последовательно", sync_time, 0))
    for c in (1, 5, 10):
        elapsed, failed = await run_async(settings, c)
        print(f"async concurrency={c}: {elapsed:.1f} с, ошибок {failed}")
        rows.append((f"async batch_chat, concurrency={c}", elapsed, failed))
    strict_lines = await strict_vs_gather(settings)

    md = [
        "# Бенчмарк sync vs async (Б3.3)",
        "",
        f"Дата: {datetime.now():%Y-%m-%d %H:%M}. Модель: `{settings.llm.default_model}` через OpenRouter. "
        f"20 промптов «объясни одним абзацем концепцию №N», `max_tokens=400`.",
        "",
        "| Режим | Общее время, с | Ускорение к sync | Ошибок |",
        "|---|---|---|---|",
        *[f"| {name} | {t:.1f} | ×{sync_time / t:.1f} | {f} |" for name, t, f in rows],
        "",
        "## batch_chat vs batch_chat_strict при ошибке на 3-м запросе",
        "",
        *strict_lines,
        "",
    ]
    RESULTS.write_text("\n".join(md), encoding="utf-8")
    print(f"\nЗаписано в {RESULTS}")


if __name__ == "__main__":
    asyncio.run(main())
