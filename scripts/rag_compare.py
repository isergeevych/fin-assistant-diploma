"""
Б5.3 — прогон 5 контрольных вопросов через обе реализации (LlamaIndex и bare-metal) + подсчёт строк кода.
Результат → docs/rag_results.md (сырые ответы), выводы — в docs/rag.md.

    python -m scripts.rag_compare
"""

import ast
import importlib.metadata as md
import time
from pathlib import Path

from app.core.config import get_settings
from app.services.rag import RAGService
from app.services.rag_baremetal import BareMetalRAG

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    ("хороший", "Какую сумму вкладов страхует АСВ, если банк лишится лицензии?"),
    ("хороший", "Звонят из службы безопасности банка и просят код из СМС — что делать?"),
    ("хороший", "Сколько стоит тариф Плюс, если платить за год?"),
    ("средний", "Мне лучше сначала досрочно гасить кредит или собрать запас на чёрный день?"),
    ("вне базы", "Как оформить загранпаспорт через Госуслуги?"),
]


def code_lines(path: Path, functions: set[str]) -> int:
    """Строки кода ingestion + query без импортов, докстрингов и пустых строк."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    src = path.read_text(encoding="utf-8").splitlines()
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            body = node.body[1:] if ast.get_docstring(node) else node.body
            lines = range(body[0].lineno, node.end_lineno + 1)
            total += sum(1 for i in lines if src[i - 1].strip() and not src[i - 1].strip().startswith("#"))
    return total


def main() -> None:
    s = get_settings()
    impls = {"LlamaIndex": RAGService(s).build(reindex=True), "bare-metal": BareMetalRAG(s).build(reindex=True)}
    out = ["# Прогон 5 вопросов: сырые ответы (scripts/rag_compare.py)\n",
           f"LLM: `{s.rag_llm_model or s.llm.default_model}`, эмбеддинги: `{s.embedding_model}`, "
           f"top_k={s.rag_top_k}, порог score={s.rag_score_threshold}\n"]
    for kind, q in QUESTIONS:
        out.append(f"## [{kind}] {q}\n")
        for name, impl in impls.items():
            started = time.perf_counter()
            r = impl.answer(q)
            ms = (time.perf_counter() - started) * 1000
            top = r["sources"][0] if r["sources"] else {"source": "—", "score": 0}
            out.append(f"**{name}** ({ms:.0f} мс, top-1: `{top['source']}` {top['score']}, "
                       f"LLM {'вызвана' if r['llm_called'] else 'НЕ вызвана (score-guard)'}):\n\n> {r['answer']}\n")
            print(f"[{kind}] {name}: top={top['source']} {top['score']} llm={r['llm_called']} | {r['answer'][:90]!r}")
    loc_li = code_lines(ROOT / "app/services/rag.py", {"build", "answer", "load_data", "_get_query_embedding",
                                                       "_get_text_embedding", "_get_text_embeddings"})
    loc_bm = code_lines(ROOT / "app/services/rag_baremetal.py", {"build", "answer"})
    versions = {p: md.version(p) for p in ["llama-index-core", "llama-index-vector-stores-qdrant",
                                            "llama-index-readers-file", "llama-index-llms-openai-like", "qdrant-client"]}
    out += ["\n## Строки кода (ingestion + query, без импортов/докстрингов)\n",
            f"- LlamaIndex (`app/services/rag.py`): {loc_li}", f"- bare-metal (`app/services/rag_baremetal.py`): {loc_bm}",
            "\n## Версии\n", *[f"- {k}: {v}" for k, v in versions.items()]]
    (ROOT / "docs" / "rag_results.md").write_text("\n".join(out), encoding="utf-8")
    print(f"LOC: LlamaIndex={loc_li}, bare-metal={loc_bm}; → docs/rag_results.md")


if __name__ == "__main__":
    main()
