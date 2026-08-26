"""轻依赖守卫：import demomcp.rag 及其子模块绝不加载 pymupdf/faiss/sentence_transformers/torch。"""

from __future__ import annotations

import importlib
import sys

RAG_SUBMODULES = [
    "demomcp.rag",
    "demomcp.rag.schemas",
    "demomcp.rag.embedder",
    "demomcp.rag.store",
    "demomcp.rag.bm25",
    "demomcp.rag.section_tree",
    "demomcp.rag.table_split",
    "demomcp.rag.segments",
    "demomcp.rag.captioner",
    "demomcp.rag.chunking",
    "demomcp.rag.pdf_parser",
    "demomcp.rag.ingest",
    "demomcp.rag.hybrid_retriever",
    "demomcp.rag.reranker",
    "demomcp.rag.query_build",
    "demomcp.rag.citing",
    "demomcp.rag.fakes",
]

HEAVY = ("pymupdf", "fitz", "faiss", "sentence_transformers", "torch")


def test_rag_import_does_not_load_heavy_deps() -> None:
    for name in RAG_SUBMODULES:
        importlib.import_module(name)
    for heavy in HEAVY:
        assert heavy not in sys.modules, f"import demomcp.rag 不应加载 {heavy}"
