"""
Б6.5 — качество ответов обеих реализаций одним судьёй: RAGAS faithfulness (ответ ⊂ найденные фрагменты) и
has_citation из 5.6. Контексты — то, что вернул tool search_knowledge_base в этом прогоне.

    python -m experiments.judge_results      # дописывает поля в experiments/results.json
"""

import asyncio
import json

from app.core.config import get_settings
from app.eval.metrics import build_judge, make_has_citation
from experiments.common import RESULTS
from ragas.metrics.collections import Faithfulness

REFUSAL_MARKERS = ("в базе ответа нет", "в базе знаний ответа", "нет фактов", "не найдено", "ничего не найдено")


async def main() -> None:
    rows = json.loads(RESULTS.read_text(encoding="utf-8"))
    judge, judge_model = build_judge(get_settings())
    faithfulness, has_citation = Faithfulness(llm=judge), make_has_citation(judge)
    for row in rows:
        contexts = [c for c in row["contexts"] if "ничего не найдено" not in c]
        if row["kind"] == "вне базы":  # утверждений из базы нет — меряем правильность отказа
            row["faithfulness"] = None
            row["correct_refusal"] = any(m in row["answer"].lower() for m in REFUSAL_MARKERS)
        else:
            row["faithfulness"] = round(float((await faithfulness.ascore(
                user_input=row["question"], response=row["answer"], retrieved_contexts=contexts or [""])).value), 3)
        row["has_citation"] = 1.0 if (await has_citation.ascore(response=row["answer"])).value == "yes" else 0.0
        row["judge"] = judge_model
        print(f"{row['impl']:<13} {row['question_id']:<10} faith={row['faithfulness']} cite={row['has_citation']} "
              f"refusal={row.get('correct_refusal')}")
    RESULTS.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
