"""
Б3.8 — запуск garak против POST /chat (baseline или after).

  python scripts/run_garak.py baseline --port 8010     # сервис запущен с SECURITY_ENABLED=false
  python scripts/run_garak.py after    --port 8010     # сервис запущен с SECURITY_ENABLED=true

Скрипт:
  - копирует eval/security/rest_config.json во временный файл с нужным портом
    (в репозитории остаётся каноничный localhost:8000);
  - запускает ровно три КОНКРЕТНЫЕ пробы (не группы — группа это сотни тысяч вызовов);
  - фиксирует seed и generations=1, чтобы baseline и after были сравнимы.
Токены авторизации (если появятся) — через переменные окружения, не в конфиге.

Отчёты garak: ~/.local/share/garak/garak_runs/<prefix>.report.{jsonl,html}
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBES = "promptinject.HijackHateHumans,encoding.InjectBase64,dan.Ablation_Dan_11_0"
SEED = 42


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("label", choices=["baseline", "after"])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--garak", default=os.environ.get("GARAK_BIN", "garak"))
    parser.add_argument("--parallel", type=int, default=5)
    args = parser.parse_args()

    config = json.loads((ROOT / "eval" / "security" / "rest_config.json").read_text(encoding="utf-8"))
    config["rest"]["RestGenerator"]["uri"] = f"http://localhost:{args.port}/chat"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(config, tmp, ensure_ascii=False)

    cmd = [
        args.garak, "--target_type", "rest", "--generator_option_file", tmp.name,
        "--probes", PROBES, "--generations", "1", "--seed", str(SEED),
        "--parallel_attempts", str(args.parallel), "--report_prefix", args.label,
    ]
    print("$", " ".join(cmd))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    sys.exit(subprocess.call(cmd, env=env))


if __name__ == "__main__":
    main()
