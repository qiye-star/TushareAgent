"""真实 PDF 入库集成测试：解析 → 摄取 → 检索 → 引用（缺 pymupdf 或样本 PDF 时自动跳过）。

覆盖「部分内容入库并验证通过」的目的：证明管线能把真实年报解析、切块、入库、
检索出带 provenance 与可解析引用的 chunk。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from demomcp.rag.citing import chunk_to_cite_ref
from demomcp.rag.hybrid_retriever import HybridRetriever
from demomcp.rag.ingest import build_index, ingest
from demomcp.rag.schemas import DocMeta, RagFilters, RetrievalPlan

_SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "比亚迪：2025年年度报告.pdf"

pytestmark = pytest.mark.skipif(
    not _SAMPLE.exists(), reason=f"样本年报不存在：{_SAMPLE}"
)


async def test_real_pdf_ingest_and_retrieve(make_settings) -> None:
    pytest.importorskip("pymupdf")
    from demomcp.rag.pdf_parser import parse_pdf  # 惰性；成功后导入

    s = make_settings(rag_embedding_model="hashing", rag_rerank_threshold=0.0)
    layout = parse_pdf(str(_SAMPLE))
    assert layout.total_pages > 0
    assert len(layout.blocks) > 0

    meta = DocMeta(
        doc_id="fy2025_byd", title="比亚迪 2025 年年度报告", company="比亚迪",
        company_code="002594.SZ", year=2025, source_pdf=str(_SAMPLE), total_pages=layout.total_pages,
    )
    index = build_index(s)
    stats = await ingest(s, index=index, doc_meta=meta, layout=layout)

    assert not stats.skipped
    assert stats.n_chunks > 0
    assert stats.n_blocks > 0
    assert index.count()[0] > 0

    retriever = HybridRetriever(index=index, config=s)
    chunks = await retriever.retrieve(
        RetrievalPlan(rewritten_query="研发投入", filters=RagFilters(company="比亚迪", year=2025))
    )
    assert len(chunks) > 0
    for c in chunks:
        assert c.doc_id == "fy2025_byd"
        assert c.metadata.get("company") == "比亚迪"
        assert c.metadata.get("year") == 2025
        assert c.metadata.get("page_start") is not None
        assert c.metadata.get("section_path")
        ref = chunk_to_cite_ref(c, ref_index=0)
        assert "比亚迪2025年报" in ref.inline  # 可解析出内联引用
