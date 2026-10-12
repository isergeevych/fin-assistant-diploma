"""
Чтение документов корпуса (Б5.2, переиспользуется в Б5.5): MD/TXT, PDF, DOCX, HTML → текст + метаданные.

Метаданные из пути и файла: source (путь относительно data/), category (папка верхнего уровня),
created_at (mtime файла), version (из имени: *_v2.md → "2"), status (archived для версий, у которых есть
более новая), file_type.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from app.chat.media import extract_docx_text, extract_pdf_text

SUPPORTED = {".md", ".txt", ".pdf", ".docx", ".html", ".htm"}
VERSION_RE = re.compile(r"_v(\d+)$")


@dataclass
class Document:
    source: str
    text: str
    metadata: dict = field(default_factory=dict)


class _HTMLText(HTMLParser):
    """Текст из <main> (или <body>); навигация, футер, скрипты и стили отбрасываются."""

    SKIP = {"nav", "footer", "script", "style", "header"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in {"p", "h1", "h2", "h3", "li", "div"}:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip() + " ")


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        return extract_pdf_text(path.read_bytes())
    if suffix == ".docx":
        return extract_docx_text(path.read_bytes())
    if suffix in {".html", ".htm"}:
        parser = _HTMLText()
        parser.feed(path.read_text(encoding="utf-8"))
        return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
    raise ValueError(f"неподдерживаемый формат: {path.name}")


def load_corpus(data_dir: Path, skip: tuple[str, ...] = ("finance",)) -> list[Document]:
    files = sorted(p for p in data_dir.rglob("*") if p.suffix.lower() in SUPPORTED and p.parts[len(data_dir.parts)] not in skip)
    versions: dict[str, int] = {}
    for p in files:  # максимальная версия у каждого документа — чтобы пометить старые как archived
        if m := VERSION_RE.search(p.stem):
            versions[VERSION_RE.sub("", p.stem)] = max(versions.get(VERSION_RE.sub("", p.stem), 0), int(m.group(1)))
    docs = []
    for p in files:
        rel = p.relative_to(data_dir).as_posix()
        m = VERSION_RE.search(p.stem)
        version = int(m.group(1)) if m else None
        status = "archived" if version is not None and version < versions[VERSION_RE.sub("", p.stem)] else "active"
        docs.append(Document(
            source=rel,
            text=extract_text(p),
            metadata={
                "source": rel,
                "file_name": p.name,
                "category": rel.split("/")[0],
                "file_type": p.suffix.lower().lstrip("."),
                "created_at": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(),
                "version": version,
                "status": status,
            },
        ))
    return docs


def naive_chunks(text: str, max_chars: int = 700) -> list[str]:
    """Наивный чанкинг: абзацы склеиваются, пока помещаются в max_chars (улучшаем в Б5.4)."""
    chunks, current = [], ""
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks
