"""
Маскирование персональных данных перед логированием (Б3.6, переиспользуется в Б3.8).

Сырой текст пользователя в логи не пишется никогда: только prompt_hash
(для корреляции одинаковых запросов) и prompt_preview = redact_pii(text)[:120].

Отличия от стартер-кода курса:
- PHONE_RU: `\\b(?:\\+7|8)` не находит «+7» после пробела — между пробелом и «+»
  нет границы слова. Заменено на lookbehind (?<![\\w+]).
- Добавлен ACCOUNT (20 цифр — номер банковского счёта): для финансового домена
  это частый PII. Проверяется раньше CARD/INN, иначе его «разрезали» бы на куски.
- Порядок шаблонов важен: длинные числовые шаблоны раньше коротких.
"""

import hashlib
import re

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b"),
    "ACCOUNT": re.compile(r"(?<!\d)\d{5}[\s\-]?\d{3}[\s\-]?\d{1}[\s\-]?\d{11}(?!\d)"),
    "CARD": re.compile(r"(?<!\d)(?:\d{4}[\s\-]?){3}\d{4}(?!\d)"),
    "PHONE_RU": re.compile(r"(?<![\w+])(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"),
    "INN": re.compile(r"(?<!\d)(?:\d{12}|\d{10})(?!\d)"),
    "PASSPORT": re.compile(r"(?<!\d)\d{2}\s?\d{2}\s\d{6}(?!\d)"),
}


def redact_pii(text: str) -> str:
    for name, pattern in PII_PATTERNS.items():
        text = pattern.sub(f"[{name}]", text)
    return text


def prompt_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def preview(text: str, limit: int = 120) -> str:
    return redact_pii(text)[:limit]
