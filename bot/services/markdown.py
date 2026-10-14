"""
Markdown модели → безопасный HTML Telegram (проблема П1 из docs/bot-testing.md: в чате были видны **звёздочки**).

Безопасность сохранена: сначала экранируется ВЕСЬ HTML из ответа модели, потом добавляются только наши теги
<b>, <code>, <pre>. Ссылки [текст](url) и голые URL кликабельными НЕ делаются — модель (или prompt injection через
документ) не может вставить в ответ фишинговую ссылку-кнопку. Незакрытые маркеры (стрим оборвался на «**») убираются.
"""

import html
import re

_CODE_BLOCK = re.compile(r"```[a-zA-Z0-9_-]*\n?(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__", re.S)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.M)
_LINK = re.compile(r"\[([^\]\n]+)\]\((\S+?)\)")
_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.M)


def to_telegram_html(text: str) -> str:
    blocks: list[str] = []

    def keep_block(m: re.Match) -> str:  # содержимое кода не трогаем остальными правилами
        blocks.append(f"<pre>{html.escape(m.group(1).rstrip())}</pre>")
        return f"\x00{len(blocks) - 1}\x00"

    def keep_inline(m: re.Match) -> str:
        blocks.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(blocks) - 1}\x00"

    text = _CODE_BLOCK.sub(keep_block, text)
    text = html.escape(text, quote=False)
    text = _INLINE_CODE.sub(keep_inline, text)  # `a**2` — звёздочки внутри кода не трогаем
    text = _HEADING.sub(r"<b>\1</b>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _LINK.sub(r"\1 (\2)", text)
    text = _HR.sub("", text)
    text = _BULLET.sub(r"\1• ", text)
    text = text.replace("**", "").replace("__", "")  # незакрытые маркеры (оборванный стрим)
    text = re.sub(r"\x00(\d+)\x00", lambda m: blocks[int(m.group(1))], text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_plain(text: str) -> str:
    """Запасной вариант, если Telegram отверг HTML: те же правила, но без тегов."""
    return html.unescape(re.sub(r"</?(b|code|pre)>", "", to_telegram_html(text)))  # сперва наши теги, потом &lt;
