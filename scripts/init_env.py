"""
Первичная настройка: .env из .env.example, служебные секреты генерируются сами. Только стандартная библиотека —
запускается до установки зависимостей проекта.

    python scripts/init_env.py --openrouter-key sk-or-v1-...            # эмбеддинги — локальная Ollama
    python scripts/init_env.py --openrouter-key sk-or-v1-... --no-ollama # эмбеддинги — тоже через OpenRouter

Уже существующий .env не перезаписывается (иначе потерялись бы данные Postgres/Qdrant с прежними паролями);
--force — перезаписать.
"""

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ("POSTGRES_PASSWORD", "INTERNAL_TOKEN", "ADMIN_TOKEN", "QDRANT_API_KEY")
# Без Ollama: эмбеддинги baai/bge-m3 через OpenRouter (та же размерность 1024; инструкция к запросу не нужна).
CLOUD_EMBEDDINGS = {
    "EMBEDDING_BASE_URL": "https://openrouter.ai/api/v1",
    "EMBEDDING_BASE_URL_DOCKER": "https://openrouter.ai/api/v1",
    "EMBEDDING_MODEL": "baai/bge-m3",
    "EMBEDDING_QUERY_INSTRUCTION": "",
}


def set_var(text: str, key: str, value: str) -> str:
    """KEY=… → KEY=value (комментарий в конце строки сохраняется); нет строки — дописывается в конец."""
    pattern = re.compile(rf"^{re.escape(key)}=[^\n#]*(#.*)?$", re.M)
    if pattern.search(text):
        return pattern.sub(lambda m: f"{key}={value}" + (f"  {m.group(1)}" if m.group(1) else ""), text, count=1)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def main() -> None:
    # консоль Windows по умолчанию cp1252 — русский вывод падал бы с UnicodeEncodeError (поймано на чистой машине)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--openrouter-key", default="", help="ключ OpenRouter (https://openrouter.ai/keys)")
    p.add_argument("--no-ollama", action="store_true", help="эмбеддинги через OpenRouter вместо локальной Ollama")
    p.add_argument("--force", action="store_true", help="перезаписать существующий .env")
    a = p.parse_args()

    env = ROOT / ".env"
    if env.exists() and not a.force:
        sys.exit(f"{env} уже есть — не трогаю (данные БД зависят от паролей в нём). Перезаписать: --force")
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in GENERATED:
        text = set_var(text, key, secrets.token_urlsafe(24))
    if a.openrouter_key:
        text = set_var(text, "LLM__API_KEY", a.openrouter_key)
    if a.no_ollama:
        for key, value in CLOUD_EMBEDDINGS.items():
            text = set_var(text, key, value)
        if a.openrouter_key:
            text = set_var(text, "EMBEDDING_API_KEY", a.openrouter_key)
    env.write_text(text, encoding="utf-8")
    print(f"создан {env.relative_to(ROOT)}: сгенерированы {', '.join(GENERATED)}")
    if not a.openrouter_key:
        print("! впишите LLM__API_KEY (ключ OpenRouter) — без него сервис не ответит на вопросы")
    print("эмбеддинги:", "OpenRouter baai/bge-m3" if a.no_ollama else
          "локальная Ollama (ollama pull qwen3-embedding:0.6b)")


if __name__ == "__main__":
    main()
