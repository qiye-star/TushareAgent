"""摄取：合成年报 → ingest → count/metadata；delete_doc 幂等；重跑幂等。"""

from __future__ import annotations

from demomcp.rag.ingest import build_index, ingest
from demomcp.rag.pdf_parser import layout_from_synthetic
from demomcp.rag.schemas import DocMeta


def _byd_docmeta() -> DocMeta:
    return DocMeta(doc_id="fy2024_byd", title="比亚迪 2024 年年度报告", company="比亚迪", company_code="002594.SZ", year=2024, source_pdf="x.pdf", total_pages=1)


def _byd_layout():
    return layout_from_synthetic(
        blocks=[
            {"page": 1, "text": "第三节 管理层讨论与分析", "size": 16.0, "kind": "text", "block_no": 0, "bbox": (0, 0, 100, 10)},
            {"page": 1, "text": "公司应对外部环境变化，加强了成本管理。", "size": 10.5, "kind": "text", "block_no": 1, "bbox": (0, 10, 100, 20)},
            {"page": 1, "text": "3.2 研发投入", "size": 14.0, "kind": "text", "block_no": 2, "bbox": (0, 20, 100, 30)},
            {"page": 1, "text": "报告期内研发投入占营业收入比例提升至百分之五。", "size": 10.5, "kind": "text", "block_no": 3, "bbox": (0, 30, 100, 40)},
            {"page": 1, "text": "其余业务稳步发展。", "size": 10.5, "kind": "text", "block_no": 4, "bbox": (0, 40, 100, 50)},
        ],
        tables=[
            {"page": 1, "caption": "表 12 研发投入情况", "headers": ["项目", "金额"], "rows": [["研发投入", "100"], ["营业收入", "2000"]], "top": 52.0, "left": 10.0},
        ],
        total_pages=1,
    )


async def test_ingest_counts_and_metadata(make_settings) -> None:
    s = make_settings(rag_embedding_model="hashing")
    index = build_index(s)
    stats = await ingest(s, index=index, doc_meta=_byd_docmeta(), layout=_byd_layout())
    assert stats.doc_id == "fy2024_byd"
    assert stats.n_chunks == 2  # 段落 1 chunk + 表格 1 chunk
    assert stats.n_table_chunks == 1
    assert stats.n_sections == 1
    assert index.count() == (2, 1)  # (chunk_store, bm25_section)

    query = index.embedder.encode_query("比亚迪 2024 研发投入")
    hits = index.chunk_store.search(query, top_k=5)
    assert len(hits) == 2
    any_para = any(h.metadata.get("block_type") == "paragraph" for h in hits)
    any_table = any(h.metadata.get("block_type") == "table" for h in hits)
    assert any_para and any_table
    for h in hits:
        assert h.metadata["company"] == "比亚迪"
        assert h.metadata["year"] == 2024
        assert h.metadata["doc_id"] == "fy2024_byd"
        assert "3.2 研发投入" in h.metadata["section_path"]
        assert h.metadata["page_start"] == 1
    tbl = next(h for h in hits if h.metadata["block_type"] == "table")
    assert tbl.metadata["table_caption"] == "表 12 研发投入情况"


async def test_ingest_delete_doc_idempotent(make_settings) -> None:
    s = make_settings(rag_embedding_model="hashing")
    index = build_index(s)
    await ingest(s, index=index, doc_meta=_byd_docmeta(), layout=_byd_layout())
    assert index.count() == (2, 1)
    index.delete_doc("fy2024_byd")
    assert index.count() == (0, 0)
    index.delete_doc("fy2024_byd")  # 再删一次不报错
    assert index.count() == (0, 0)


async def test_ingest_rerun_idempotent(make_settings) -> None:
    s = make_settings(rag_embedding_model="hashing")
    index = build_index(s)
    await ingest(s, index=index, doc_meta=_byd_docmeta(), layout=_byd_layout())
    first = index.count()
    await ingest(s, index=index, doc_meta=_byd_docmeta(), layout=_byd_layout())
    assert index.count() == first  # 重跑不重复


def _two_sec_layout():
    """两个不同页的叶子节（3.2 / 3.3），用于断言 section_store 每节唯一键不互相覆盖。"""
    return layout_from_synthetic(
        blocks=[
            {"page": 1, "text": "第三节 管理层讨论与分析", "size": 18.0, "kind": "text", "block_no": 0, "bbox": (0, 0, 100, 5)},
            {"page": 1, "text": "3.2 研发投入", "size": 14.0, "kind": "text", "block_no": 1, "bbox": (0, 5, 100, 10)},
            {"page": 1, "text": "研发投入占比上升。", "size": 10.5, "kind": "text", "block_no": 2, "bbox": (0, 10, 100, 20)},
            {"page": 2, "text": "3.3 营业收入", "size": 14.0, "kind": "text", "block_no": 3, "bbox": (0, 5, 100, 10)},
            {"page": 2, "text": "营业收入增长。", "size": 10.5, "kind": "text", "block_no": 4, "bbox": (0, 10, 100, 20)},
        ],
        tables=[], total_pages=2,
    )


async def test_ingest_section_store_distinct_per_section(make_settings) -> None:
    """每个节在 section_store 有唯一键（此前都用 chunk_index=0 相互覆盖只剩 1 条）。"""
    s = make_settings(rag_embedding_model="hashing")
    index = build_index(s)
    await ingest(s, index=index, doc_meta=_byd_docmeta(), layout=_two_sec_layout())
    assert index.bm25_section.count() == 2
    assert index.section_store.count() == 2  # 修复前只会是 1
