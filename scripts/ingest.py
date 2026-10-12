"""Б5.5 — индексация базы знаний: `python scripts/ingest.py data/`. Логика — app/services/ingestion.py
(там же её вызывает POST /documents/upload и одноразовый сервис ingest в docker compose)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ingestion import main  # noqa: E402

if __name__ == "__main__":
    main()
