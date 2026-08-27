"""dba 一键迁移/构建 RAG 索引：读 .env → 摄取语料 → 写 Milvus‑Lite + SQLite（落盘）。

之后服务启动 `build_runtime_retriever` 会从磁盘秒级加载，不再每次重打 bge-m3 嵌入 API。

用法：
  .venv/Scripts/python.exe scripts/dba_rag.py [--corpus docs] [--rebuild] [--hashing]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path

from demomcp.config.settings import Settings
from demomcp.rag import persist
from demomcp.rag.ingest import build_index
from demomcp.rag.runtime import _ingest_dir, _resolve_corpus_dir


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=None, help="语料目录（默认用 RAG_CORPUS_DIR）")
    ap.add_argument("--rebuild", action="store_true", help="清空后重建")
    ap.add_argument("--hashing", action="store_true", help="离线 hashing（不落 Milvus，仅验证）")
    args = ap.parse_args()

    s = Settings()
    if args.hashing:
        s = Settings(_env_file=None)
        s.rag_embedding_model = "hashing"
        s.rag_use_real = False
        s.rag_corpus_dir = args.corpus or "docs"

    base = Path(s.rag_vector_store_path) if s.rag_use_real and s.rag_vector_store_path else None
    if args.rebuild and base is not None:
        shutil.rmtree(base, ignore_errors=True)
        print(f"[dba] 已清空 {base}")

    index = build_index(s)

    corpus = Path(args.corpus) if args.corpus else _resolve_corpus_dir(s)
    if corpus is not None and corpus.is_dir():
        n = await _ingest_dir(s, index, corpus)
        print(f"[dba] 已摄取 {n} 份（chunk={index.count()[0]}, section={index.count()[1]}）")
    else:
        print(f"[dba] 无语料目录（corpus={corpus}），跳过摄取")

    if base is not None and s.rag_use_real:
        try:
            persist.save_index(index, base)
            print(f"[dba] 索引已写入 {base}")
        except Exception as exc:  # noqa: BLE001
            print(f"[dba] 索引写入失败：{exc}")
            return 1
    else:
        print("[dba] 非 real（hashing）或未配置目录：未落 Milvus")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
