"""
Б3.7 — проверка последнего прогона eval против порогов (exit 1 = релизить нельзя).

  python eval/check_thresholds.py                       # последний eval/runs/*.json
  python eval/check_thresholds.py --run eval/runs/2026-10-12.json
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def latest_run(runs_dir: Path) -> Path:
    runs = sorted(runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not runs:
        sys.exit(f"Нет прогонов в {runs_dir}: сначала запустите eval/run_evaluation.py")
    return runs[-1]


def check(aggregates: dict, thresholds: dict) -> list[str]:
    failures = []
    for metric, rule in thresholds.items():
        if metric not in aggregates:
            failures.append(f"{metric}: метрики нет в прогоне")
            continue
        value = aggregates[metric]
        if "min" in rule and value < rule["min"]:
            failures.append(f"{metric} = {value} < порога {rule['min']}")
        if "max" in rule and value > rule["max"]:
            failures.append(f"{metric} = {value} > порога {rule['max']}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=None)
    parser.add_argument("--thresholds", type=Path, default=ROOT / "eval" / "thresholds.yaml")
    args = parser.parse_args()

    run_path = args.run or latest_run(ROOT / "eval" / "runs")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    thresholds = yaml.safe_load(args.thresholds.read_text(encoding="utf-8"))
    failures = check(run["aggregates"], thresholds)

    print(f"Прогон: {run_path.name} (model={run['model_under_test']}, judge={run['judge_model']})")
    if failures:
        print("❌ Пороги нарушены:")
        for f in failures:
            print(f"  - {f}")
        worst = sorted(run["items"], key=lambda i: i["scores"]["correctness"])[:3]
        print("Худшие кейсы:", ", ".join(f"{i['id']} (correctness={i['scores']['correctness']})" for i in worst))
        sys.exit(1)
    print("✅ Все пороги выполнены — можно релизить")


if __name__ == "__main__":
    main()
