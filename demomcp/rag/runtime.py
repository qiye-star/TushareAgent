"""运行时 RAG retriever 工厂：按配置构建 HybridRetriever，并把语料目录（默认 docs/）的年报 PDF 摄取成索引。

- **进程级缓存 + 锁**：避免 web 每请求（每 Agent）重建/重摄入；首次构建+摄入后复用同一 retriever。
- **默认语料目录**：`RAG_CORPUS_DIR` 为空 → `PROJECT_ROOT/docs`（用户年报 PDF 所在）；也兼容 `data/corpus/{company}/{year}/` 布局。
- 重依赖（pymupdf 等）在函数体内懒加载；任何失败回退 None/空索引，缺 rag-full/无语料/解析失败都不崩主链路。
- `demomcp/rag/__init__.py` 保持无 import。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from demomcp.config.env import PROJECT_ROOT
from demomcp.config.settings import Settings

# —— 进程级缓存（retriever 复用，避免每请求重摄入）——
_CACHE: dict[str, Any] = {}
_CACHE_LOCK = asyncio.Lock()


def _doc_meta_from_path(pdf: Path, *, base: Path) -> Any:
    """从文件名/子目录启发式提取 DocMeta（company+year）。真实摄取可另行传入精确元数据。"""
    from demomcp.rag.schemas import DocMeta

    name = pdf.stem
    year_match = re.search(r"(?:19|20)\d{2}", name)
    year = int(year_match.group(0)) if year_match else 0
    # 公司名：优先取目录名（data/corpus/{company}/{year}/），否则从文件名中'：'/'年'前截取
    rel = pdf.relative_to(base)
    parts = rel.parts
    company = next((p for p in parts[:-1] if p not in ("", "corpus", "docs", "年报")), "")
    if not company:
        company = re.split(r"[：:\-—_年]", name)[0].strip()
    return DocMeta(
        doc_id=name,
        title=name,
        company=company,
        company_code="",
        year=year,
        report_type="annual_report",
        source_pdf=str(pdf),
        source_url=None,
        total_pages=None,
        parse_tool="pymupdf",
        ingested_at="",
    )


def _is_report_name(name: str) -> bool:
    """文件名像年报才摄取：含「年度报告/年报」，或「4位年份+报告」。避免非报告 PDF 污染索引。"""
    cleaned = Path(name).stem
    if any(m in cleaned for m in ("年度报告", "年报")):
        return True
    return bool(re.search(r"(?:19|20)\d{2}", cleaned)) and "报告" in cleaned


def _resolve_corpus_dir(config: Settings) -> Path | None:
    """返回要摄入的语料目录；仅在显式配置 RAG_CORPUS_DIR 时摄入（默认不自动扫 docs/，避免测试/无索引时加载 pymupdf）。"""
    raw = (config.rag_corpus_dir or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p if p.is_dir() else None


async def _ingest_dir(config: Settings, index: Any, base: Path) -> int:
    """best-effort 摄取 base 下所有 PDF（含子目录）；跳过解析/章节为空的。返回成功摄取份数。"""
    from demomcp.rag.ingest import ingest
    from demomcp.rag.pdf_parser import parse_pdf

    n = 0
    for pdf in sorted(base.rglob("*.pdf")):
        if not _is_report_name(pdf.name):
            print(f"[rag] 跳过非年报 {pdf.relative_to(base)}")
            continue
        try:
            layout = parse_pdf(str(pdf), table_engine=config.rag_table_engine)
            if layout.total_pages < 1:
                continue
            await ingest(config, index=index, doc_meta=_doc_meta_from_path(pdf, base=base), layout=layout)
            n += 1
            print(f"[rag] 已摄取 {pdf.relative_to(base)}（chunk={index.count()[0]}）")
        except Exception as exc:  # noqa: BLE001 - 单个 PDF 失败则跳过
            print(f"[rag] 摄取跳过 {pdf.name}：{exc}")
    return n


async def build_runtime_retriever(config: Settings) -> Any | None:
    """构建（并缓存）HybridRetriever；可选把语料目录的年报 PDF 摄取进索引。

    返回 retriever（可检索）；构建失败回退 None（图上不接 RAG）。不捕获 BaseException（取消透传）。
    """
    async with _CACHE_LOCK:
        if _CACHE.get("retriever") is not None:
            return _CACHE["retriever"]

    try:
        from demomcp.rag import persist
        from demomcp.rag.hybrid_retriever import HybridRetriever
        from demomcp.rag.ingest import build_index
        from demomcp.rag.reranker import build_reranker
    except Exception as exc:  # noqa: BLE001 - 缺依赖/模块问题
        print(f"[rag] 运行时 RAG 构建跳过（导入失败）：{exc}")
        return None

    # —— 持久化：real 且已有「Milvus + SQLite」索引 → 秒级加载；否则摄取 + 落盘 ——
    base = Path(config.rag_vector_store_path) if config.rag_use_real and config.rag_vector_store_path else None
    if base is not None and persist.has_index(base):
        try:
            index = persist.load_index(config, base)
            print(f"[rag] 索引已加载（chunk={index.chunk_store.count()}）")
        except Exception as exc:  # noqa: BLE001 - 加载失败 → 走重新摄取
            print(f"[rag] 索引加载失败，重走摄取：{exc}")
            index = build_index(config)
            await _ingest_and_save(config, index, base)
    elif base is not None:
        try:
            index = build_index(config)
        except Exception as exc:  # noqa: BLE001
            print(f"[rag] 构建索引失败，回退 None：{exc}")
            return None
        await _ingest_and_save(config, index, base)
    else:
        # 离线（hashing）→ 内存索引
        try:
            index = build_index(config)
        except Exception as exc:  # noqa: BLE001
            print(f"[rag] 构建索引失败，回退 None：{exc}")
            return None
        corpus = _resolve_corpus_dir(config)
        if corpus is not None:
            await _ingest_dir(config, index, corpus)
            print(f"[rag] corpus 已摄入（chunk={index.count()[0]}）")

    retriever = HybridRetriever(index=index, reranker=build_reranker(config), config=config)
    async with _CACHE_LOCK:
        _CACHE["retriever"] = retriever
    return retriever


async def _ingest_and_save(config: Settings, index, base: Path) -> None:
    """摄取语料并保存 BM25/元数据（chunk/节文本已由 MilvusVectorStore.add 落 RelStore）。"""
    corpus = _resolve_corpus_dir(config)
    if corpus is not None:
        await _ingest_dir(config, index, corpus)
    try:
        from demomcp.rag import persist

        persist.save_index(index, base)
        print(f"[rag] 索引已保存到 {base}")
    except Exception as exc:  # noqa: BLE001 - 保存失败不阻检索
        print(f"[rag] 索引保存失败：{exc}")


def _reset_cache() -> None:
    """测试用：清空进程级缓存。"""
    _CACHE.pop("retriever", None)
