"""轻依赖守卫：import demomcp.rag 及其子模块绝不加载 pymupdf/faiss/sentence_transformers/torch。

用**子进程**执行，避免本进程其它测试（如 MilvusVectorStore 测试会 import pymilvus，连带把 `faiss`
放进 sys.modules）污染判断。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
    "demomcp.rag.persist",
    "demomcp.rag.fakes",
]

HEAVY = ("pymupdf", "fitz", "faiss", "sentence_transformers", "torch")


def test_rag_import_does_not_load_heavy_deps() -> None:
    imports = "\n".join(f"importlib.import_module({name!r})" for name in RAG_SUBMODULES)
    checks = "\n".join(f"assert {h!r} not in sys.modules, {h!r}" for h in HEAVY)
    script = f"import importlib, sys\n{imports}\n{checks}"
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    subprocess.run([sys.executable, "-c", script], check=True, env=env, cwd=str(ROOT))
