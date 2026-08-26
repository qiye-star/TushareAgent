"""引用溯源：cite_ref / 内联标记 / to_citation / reflist；表格块带题注；页码区间。"""

from __future__ import annotations

from demomcp.rag.citing import (
    chunk_to_cite_ref,
    format_reflist,
    to_citation,
)
from demomcp.rag.schemas import RagChunk


def _chunk(block_type="paragraph", page_start=42, page_end=42, caption=None):
    return RagChunk(
        doc_id="fy2024_byd", doc_title="比亚迪 2024 年年度报告", chunk_index=3, text="报告期内研发投入占比提升。",
        metadata={
            "company": "比亚迪", "company_code": "002594.SZ", "year": 2024,
            "doc_id": "fy2024_byd", "section_path": ["第三节 管理层讨论与分析", "3.2 研发投入"],
            "heading": "3.2 研发投入", "page_start": page_start, "page_end": page_end,
            "block_type": block_type, "table_headers": ["项目", "金额"], "table_caption": caption,
        },
    )


def test_chunk_to_cite_ref_paragraph_inline() -> None:
    ref = chunk_to_cite_ref(_chunk("paragraph", 42, 42, None))
    assert ref.company == "比亚迪"
    assert ref.year == 2024
    assert ref.page == "42"
    assert ref.section == "第三节 管理层讨论与分析 → 3.2 研发投入"
    assert ref.inline == "[比亚迪2024年报 - 第42页 3.2 研发投入]"


def test_table_block_inline_has_caption() -> None:
    ref = chunk_to_cite_ref(_chunk("table", 42, 42, "表 12 研发投入情况"))
    assert "表 12 研发投入情况" in ref.inline
    assert ref.block_type == "table"


def test_page_range_str() -> None:
    ref = chunk_to_cite_ref(_chunk("paragraph", 42, 43, None))
    assert ref.page == "42-43"


def test_to_citation_fields() -> None:
    c = _chunk("table", 42, 42, "表 12 研发投入情况")
    cit = to_citation("claim1", c, ref_index=1)
    assert cit.claim_id == "claim1"
    assert cit.source_type == "rag"
    assert cit.source_id == "fy2024_byd#3"
    assert cit.section_path == "第三节 管理层讨论与分析 → 3.2 研发投入"
    assert cit.page_end == "42"
    assert cit.block_type == "table"
    assert cit.company == "比亚迪"
    assert cit.year == 2024
    assert cit.ref_index == 1
    assert cit.inline_marker == "[比亚迪2024年报 - 第42页 3.2 研发投入 · 表 12 研发投入情况]"


def test_format_reflist() -> None:
    refs = [chunk_to_cite_ref(_chunk("table", 42, 42, "表 12 研发投入情况"), ref_index=0),
            chunk_to_cite_ref(_chunk("paragraph", 43, 43, None), ref_index=1)]
    text = format_reflist(refs)
    assert text.startswith("[1] 比亚迪 2024 年年度报告，第 42 页，第三节 管理层讨论与分析 → 3.2 研发投入")
    assert "[2] 比亚迪 2024 年年度报告，第 43 页" in text
