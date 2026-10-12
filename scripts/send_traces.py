"""
Б5.6 — нагрузка для Phoenix: 20+ запросов к POST /rag/query работающего сервиса.

    python -m scripts.send_traces --url http://127.0.0.1:8010 --n 24

Вопросы: retrieval_dataset (Б5.4) + несколько «вне базы» (должны дать отказ). Сервис должен быть поднят с
PHOENIX_LLAMAINDEX_TRACING=true — тогда в Phoenix (http://127.0.0.1:6006) у каждого запроса дерево
query → retrieve → synthesize/LLM. Скрипт печатает только сводку: ответы смотреть в трейсах.
"""

import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REFUSAL_PREFIX = "По базе не нашёл"  # = app.services.rag.REFUSAL (не импортируем: скрипту хватает httpx)
OUT_OF_SCOPE = [
    "Как сварить борщ на говяжьем бульоне?",
    "Кто выиграл чемпионат мира по футболу в 2018 году?",
    "Какой курс биткоина будет через месяц?",
    "Напиши стихотворение про осень",
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8010")
    p.add_argument("--n", type=int, default=24)
    args = p.parse_args()
    items = json.loads((ROOT / "tests" / "eval" / "retrieval_dataset.json").read_text(encoding="utf-8"))["items"]
    questions = [i["question"] for i in items][: max(args.n - len(OUT_OF_SCOPE), 0)] + OUT_OF_SCOPE
    ok = refused = failed = 0
    with httpx.Client(base_url=args.url, timeout=180) as client:
        for n, q in enumerate(questions, 1):
            started = time.perf_counter()
            r = client.post("/rag/query", json={"question": q})
            ms = (time.perf_counter() - started) * 1000
            if r.status_code != 200:
                failed += 1
                print(f"{n:>2}. HTTP {r.status_code} {q[:60]}")
                continue
            body = r.json()
            # источники отдаются и при отказе (прозрачность) — отказ определяем по тексту ответа
            is_refusal = body["answer"].startswith(REFUSAL_PREFIX)
            refused += is_refusal
            ok += not is_refusal
            mark = "отказ" if is_refusal else "ответ"
            print(f"{n:>2}. {ms:>6.0f} мс  {mark}  top={body['top_score']:.2f}  {q[:60]}")
    print(f"итого: {len(questions)} запросов, ответов {ok}, отказов {refused}, ошибок {failed}")


if __name__ == "__main__":
    main()
