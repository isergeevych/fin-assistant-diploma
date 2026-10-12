"""
Загрузка промптов из файлов.

- system_<version>.j2 — системный промпт (Jinja2). Версионирование — по имени
  файла (system_v1.j2 → system_v2.j2) и git-истории.
- tools/<tool_name>.md — длинные description для tools: это тоже промпт,
  модель читает его на каждом запросе.

Шаблоны рендерятся в SandboxedEnvironment со StrictUndefined: опечатка в
переменной сразу падает, а пользовательский текст никогда не становится
текстом шаблона (только значением переменной) — SSTI исключена.
"""

from functools import lru_cache
from pathlib import Path

from jinja2 import FileSystemLoader, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

PROMPTS_DIR = Path(__file__).parent

_env = SandboxedEnvironment(
    loader=FileSystemLoader(PROMPTS_DIR),
    undefined=StrictUndefined,
    keep_trailing_newline=False,
)


def render_system_prompt(version: str = "v1", **context) -> str:
    return _env.get_template(f"system_{version}.j2").render(**context).strip()


@lru_cache(maxsize=32)
def load_tool_description(tool_name: str) -> str:
    return (PROMPTS_DIR / "tools" / f"{tool_name}.md").read_text(encoding="utf-8").strip()
