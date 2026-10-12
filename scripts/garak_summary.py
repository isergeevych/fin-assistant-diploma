"""
Б3.8 — сводка garak по сырому report.jsonl: для каждой пробы и детектора — сколько атак, сколько отсечено
защитой ДО модели (HTTP 400/502 → skip_codes → пустой output), сколько дошло до модели и сколько из них успешны.

Сводка garak в консоли считает долю успеха только по дошедшим до модели попыткам — заблокированные из
знаменателя выпадают, и «after» выглядит хуже, чем есть. Здесь знаменатель общий: все атаки пробы.

    python -m scripts.garak_summary ~/.local/share/garak/garak_runs/baseline.report.jsonl \
                                    ~/.local/share/garak/garak_runs/after.report.jsonl
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

THRESHOLD = 0.5  # garak eval_threshold по умолчанию


def summarize(path: Path) -> dict:
    stats: dict = defaultdict(lambda: {"total": 0, "blocked": 0, "hits": defaultdict(int)})
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("entry_type") != "attempt" or row.get("status") != 2:
            continue
        s = stats[row["probe_classname"]]
        s["total"] += 1
        outputs = [o for o in row.get("outputs", []) if o and (o.get("text") if isinstance(o, dict) else o)]
        if not outputs:
            s["blocked"] += 1
            continue
        for detector, scores in (row.get("detector_results") or {}).items():
            if any(sc is not None and sc >= THRESHOLD for sc in scores):
                s["hits"][detector] += 1
    return stats


def main() -> None:
    runs = {Path(p).name.split(".")[0]: summarize(Path(p).expanduser()) for p in sys.argv[1:]}
    print("| Прогон | Проба | Детектор | Атак | Отсечено до модели | Дошло до модели | Успешных атак | Доля успеха (от всех атак) |")
    print("|---|---|---|---|---|---|---|---|")
    for label, stats in runs.items():
        for probe, s in sorted(stats.items()):
            reached = s["total"] - s["blocked"]
            detectors = sorted(s["hits"]) or ["—"]
            for det in detectors:
                hits = s["hits"].get(det, 0)
                print(f"| {label} | {probe} | {det} | {s['total']} | {s['blocked']} | {reached} | {hits} | "
                      f"{hits / s['total']:.1%} |")


if __name__ == "__main__":
    main()
