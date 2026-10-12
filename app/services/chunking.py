"""
Б5.4 — три стратегии chunking (LlamaIndex node parsers).

  fixed_size — TokenTextSplitter: режет по токенам, не глядя на границы предложений (baseline);
  recursive  — SentenceSplitter: сначала по абзацам (\\n\\n), затем по русским предложениям —
               аналог LangChain RecursiveCharacterTextSplitter (НЕ HierarchicalNodeParser: тот
               строит 3-уровневое дерево 2048/512/128 для AutoMergingRetriever — другая ниша);
  semantic   — SemanticSplitterNodeParser: граница там, где резко меняется смысл соседних
               предложений (расстояние эмбеддингов выше 95-го перцентиля); embed_model — тот же, что в индексе.
"""

import re

from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.node_parser import SemanticSplitterNodeParser, SentenceSplitter, TokenTextSplitter

# Граница предложения: . ! ? … перед пробелом и заглавной буквой/цифрой/кавычкой. Не режет «1,4 млн ₽.»
# посреди числа и не путается с «т. е.» перед строчной буквой.
_SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=[«\"(A-ZА-ЯЁ0-9-])")


def ru_sentences(text: str) -> list[str]:
    return [s for s in _SENT_RE.split(text) if s.strip()]


def fixed_size(chunk_size: int = 512, chunk_overlap: int = 64) -> TokenTextSplitter:
    return TokenTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def recursive(chunk_size: int = 512, chunk_overlap: int = 64) -> SentenceSplitter:
    return SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                            paragraph_separator="\n\n", chunking_tokenizer_fn=ru_sentences)


def semantic(embed_model: BaseEmbedding) -> SemanticSplitterNodeParser:
    return SemanticSplitterNodeParser(buffer_size=1, breakpoint_percentile_threshold=95,
                                      embed_model=embed_model, sentence_splitter=ru_sentences)


def build_splitter(strategy: str, embed_model: BaseEmbedding, chunk_size: int = 512, chunk_overlap: int = 64):
    if strategy == "fixed":
        return fixed_size(chunk_size, chunk_overlap)
    if strategy == "recursive":
        return recursive(chunk_size, chunk_overlap)
    if strategy == "semantic":
        return semantic(embed_model)
    raise ValueError(f"неизвестная стратегия {strategy!r}: fixed | recursive | semantic")
