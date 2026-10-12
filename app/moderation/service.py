"""
Б4.4 — модерация контента в backend (в боте её нет).

Слои (от дешёвого к дорогому):
  1. keywords — regex из moderation_keywords.yaml: бесплатно, мгновенно, отсекает грубое;
  2. openai   — omni-moderation-latest (опционально, если задан OPENAI_API_KEY): flagged
                + собственные пороги по категориям.

Отличие от input_validator (Б3.8): тот ловит АТАКИ на модель (инъекции, кодировки) → 400;
модерация — НЕДОПУСТИМЫЙ КОНТЕНТ (мошенничество, оскорбления, насилие) → 403.

Инциденты логируются без сырого текста: sha256[:16] + маскированный превью + категории + слой.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import structlog
import yaml
from openai import AsyncOpenAI

from app.observability.pii import preview, prompt_hash

log = structlog.get_logger("moderation")
KEYWORDS_FILE = Path(__file__).with_name("moderation_keywords.yaml")
# Свои пороги поверх flagged: финансовый бот строже к мошенничеству-подобному и харассменту
OPENAI_THRESHOLDS = {"harassment": 0.5, "hate": 0.4, "violence": 0.6, "illicit": 0.4, "self-harm": 0.3}


@dataclass(frozen=True)
class ModerationResult:
    allowed: bool
    categories: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    blocked_by: str = ""


ALLOWED = ModerationResult(allowed=True)


@lru_cache(maxsize=1)
def load_rules(path: Path = KEYWORDS_FILE) -> dict[str, list[re.Pattern[str]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["categories"]
    return {cat: [re.compile(p, re.IGNORECASE) for p in patterns] for cat, patterns in raw.items()}


class ModerationService:
    def __init__(self, openai_client: AsyncOpenAI | None = None, enabled: bool = True):
        self.rules = load_rules()
        self.openai = openai_client
        self.enabled = enabled

    async def check_input(self, content: str) -> ModerationResult:
        return await self._check(content, direction="input")

    async def check_output(self, content: str) -> ModerationResult:
        return await self._check(content, direction="output")

    async def _check(self, text: str, direction: str) -> ModerationResult:
        if not self.enabled or not text.strip():
            return ALLOWED
        result = self._keywords(text)
        if result.allowed and self.openai is not None:
            result = await self._openai(text)
        if not result.allowed:
            log.warning("moderation.blocked", direction=direction, layer=result.blocked_by,
                        categories=result.categories, text_hash=prompt_hash(text), text_preview=preview(text))
        return result

    def _keywords(self, text: str) -> ModerationResult:
        hits = {cat: p.pattern for cat, patterns in self.rules.items() for p in patterns if p.search(text)}
        if not hits:
            return ALLOWED
        return ModerationResult(False, sorted(hits), [f"{c}: /{p}/" for c, p in sorted(hits.items())], "keywords")

    async def _openai(self, text: str) -> ModerationResult:
        try:
            response = await self.openai.moderations.create(model="omni-moderation-latest", input=text)
        except Exception as e:  # noqa: BLE001 — недоступность модерации не должна ронять чат (fail-open + лог)
            log.warning("moderation.openai_unavailable", error=type(e).__name__)
            return ALLOWED
        r = response.results[0]
        scores = r.category_scores.model_dump(by_alias=True)
        over = sorted(
            cat for cat, threshold in OPENAI_THRESHOLDS.items()
            if any(k.startswith(cat) and (v or 0) >= threshold for k, v in scores.items())
        )
        if r.flagged or over:
            cats = over or sorted(k for k, v in r.categories.model_dump(by_alias=True).items() if v)
            return ModerationResult(False, cats, [f"openai flagged={r.flagged}"], "openai")
        return ALLOWED
