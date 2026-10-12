"""
Б5.5 — POST /documents/upload: загрузка документа в базу знаний, индексация в фоне (202 Accepted).

Безопасность:
  - только с X-Admin-Token: загрузка в ОБЩУЮ базу знаний = возможность «отравить» ответы всем
    пользователям (OWASP LLM04, data poisoning) — это не действие для обычного клиента;
  - имя файла генерируется сервером (uuid + безопасный slug + расширение из allowlist) —
    path traversal через «../../app/main.py» невозможен; категория — только из allowlist;
  - тип по сигнатуре байтов (как в Б4.3), лимит 10 МБ;
  - индексация — IngestionPipeline с DocstoreStrategy.UPSERTS (тот же код, что у scripts/ingest.py).
"""

import re
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from app.chat.media import DOCX_MIME, MediaError, read_limited, sniff_mime
from app.deps.auth import require_admin
from app.deps.providers import SettingsDep

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_admin)])
log = structlog.get_logger("documents")

CATEGORIES = {"taxes", "deposits_credits", "budgeting", "fraud_security", "app_faq", "uploads"}
EXT_BY_MIME = {"application/pdf": ".pdf", DOCX_MIME: ".docx"}
TEXT_EXT = {".md", ".txt", ".html", ".htm"}


def safe_name(original: str, ext: str) -> str:
    stem = re.sub(r"[^\w\-]+", "_", Path(original).stem, flags=re.UNICODE).strip("_")[:40] or "document"
    return f"{stem}_{uuid.uuid4().hex[:8]}{ext}"


def _ingest(path: Path) -> None:
    from app.services.ingestion import run_ingestion

    report = run_ingestion([path])
    log.info("documents.ingested", file=path.name, changed=report.changed, nodes=report.nodes_written,
             failed=report.failed)


@router.post("/upload", status_code=202, summary="Загрузить документ в базу знаний (индексация в фоне)",
             responses={202: {"description": "Принято: документ сохранён, индексация запущена"},
                        401: {"description": "Нет X-Admin-Token"}, 413: {"description": "Больше 10 МБ"},
                        415: {"description": "Неподдерживаемый тип"}})
async def upload(background: BackgroundTasks, settings: SettingsDep, file: UploadFile = File(...),
                 category: str = Form("uploads")) -> dict:
    if category not in CATEGORIES:
        raise HTTPException(status_code=422, detail=f"category должна быть одной из {sorted(CATEGORIES)}")
    try:
        data = await read_limited(file)
    except MediaError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e
    mime = sniff_mime(data)
    ext = EXT_BY_MIME.get(mime or "")
    if ext is None:
        declared = Path(file.filename or "").suffix.lower()
        if declared in TEXT_EXT and b"\x00" not in data[:4096]:  # текст: без двоичных нулей
            ext = declared
        else:
            raise HTTPException(status_code=415, detail="Поддерживаются PDF, DOCX, MD, TXT, HTML")
    target_dir = settings.data_dir / category
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name(file.filename or "document", ext)
    target.write_bytes(data)
    background.add_task(_ingest, target)
    log.info("documents.accepted", file=target.name, category=category, size=len(data))
    return {"status": "accepted", "document": f"{category}/{target.name}"}
