"""
Б3.8 — проверка входа ДО похода в LLM.

Решение по сценарию проекта: подозрительный ввод БЛОКИРУЕТСЯ (HTTP 400,
{"error": {"code": "input_rejected"}}), а не подменяется «безопасным» ответом.
Финансовый ассистент работает с личными данными пользователя, и ложный отказ
здесь дешевле, чем успешная инъекция: пользователь переформулирует вопрос,
а утечку данных уже не отменить.

Слои проверки:
1. длина — отсекает «простыни» с запрятанными инструкциями и тратой токенов;
2. невидимые и служебные символы Unicode (tag-символы U+E0000–U+E007F,
   zero-width, bidi-override) — так прячут инструкции (garak goodside.Tag);
3. доля непечатаемых символов — признак атак кодировками;
4. длинные base64-блоки — encoding.InjectBase64 прячет инструкцию в base64;
5. blocklist известных шаблонов инъекций на английском и русском.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

INJECTION_PATTERNS: Final[list[re.Pattern[str]]] = [
    # до 5 слов между ignore и instructions: «IGNORE ANY PREVIOUS AND FOLLOWING INSTRUCTIONS» (garak HijackHateHumans)
    re.compile(r"\bignore\b(?:\W+\w+){0,5}?\W+(instructions?|prompts?|rules?|directions?|guidelines)\b", re.I),
    re.compile(r"\bdisregard\s+(all\s+|the\s+|any\s+)?(system|previous|above|prior|earlier)\b", re.I),
    re.compile(r"\byou\s+are\s+now\s+(a|an|the|dan|in|free|do anything now)\b", re.I),
    re.compile(r"\bforget\s+(everything|all|your|previous|prior)\b", re.I),
    re.compile(r"\b(jailbroken?|jailbreak|developer\s+mode|godmode|god\s+mode|dan\s+mode)\b", re.I),
    re.compile(r"\bdo\s+anything\s+now\b", re.I),
    re.compile(r"\b(print|reveal|show|repeat|output)\s+(your|the)\s+(system\s+)?(prompt|instructions)\b", re.I),
    re.compile(r"\bstop\s+everything\b.*\b(print|say|output)\b", re.I | re.S),
    # русские формулировки
    re.compile(r"(игнорируй|игнорировать|забудь|отмени|не\s+обращай\s+внимания\s+на)\s+(все\s+|всё\s+)?(предыдущ|прошл|прежн|системн|свои|ранее)", re.I),
    re.compile(r"(выведи|покажи|раскрой|повтори|напиши)\s+(свой|свои|твой|твои|весь|системн)\S*\s+(промпт|инструкци|правил)", re.I),
    re.compile(r"(ты\s+теперь|теперь\s+ты)\s+(dan|без\s+ограничений|в\s+режиме)", re.I),
    re.compile(r"режим\s+(разработчика|бога|без\s+ограничений)", re.I),
]

MAX_INPUT_CHARS: Final[int] = 4000
NON_PRINTABLE_RATIO_LIMIT: Final[float] = 0.10
BASE64_BLOCK_RE: Final = re.compile(r"(?:[A-Za-z0-9+/]{4}){10,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
SUSPICIOUS_CODEPOINTS: Final = (
    (0xE0000, 0xE007F),  # Unicode tags — невидимый текст
    (0x200B, 0x200F),    # zero-width и направляющие метки
    (0x202A, 0x202E),    # bidi embedding/override
    (0x2060, 0x2064),    # word joiner и невидимые операторы
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None
    rule: str | None = None


def _suspicious_char(c: str) -> bool:
    cp = ord(c)
    return any(lo <= cp <= hi for lo, hi in SUSPICIOUS_CODEPOINTS)


def validate_input(text: str) -> ValidationResult:
    if len(text) > MAX_INPUT_CHARS:
        return ValidationResult(False, f"ввод длиннее {MAX_INPUT_CHARS} символов", rule="length")

    if any(_suspicious_char(c) for c in text):
        return ValidationResult(False, "невидимые/служебные символы Unicode", rule="unicode")

    non_printable = sum(1 for c in text if not c.isprintable() and c not in "\n\r\t")
    if non_printable / max(len(text), 1) > NON_PRINTABLE_RATIO_LIMIT:
        return ValidationResult(False, "высокая доля непечатаемых символов", rule="encoding")

    if BASE64_BLOCK_RE.search(text):
        return ValidationResult(False, "длинный base64-блок", rule="base64")

    normalized = unicodedata.normalize("NFKC", text)  # «ｉｇｎｏｒｅ» → «ignore»
    for pattern in INJECTION_PATTERNS:
        if pattern.search(normalized):
            return ValidationResult(False, "шаблон prompt injection", rule="injection")

    return ValidationResult(True)
