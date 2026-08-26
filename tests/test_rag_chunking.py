"""切片：句界感知 dense 分块（恒有 overlap、硬切不崩）、块感知 chunk_blocks、整节截断。"""

from __future__ import annotations

from demomcp.rag.chunking import chunk_blocks, chunk_dense_block, chunk_section
from demomcp.rag.schemas import Block, SectionNode


def _para(text: str) -> Block:
    return Block(block_type="paragraph", section_path=["第三节"], page_start=1, page_end=1, text=text)


def test_chunk_dense_block_has_overlap() -> None:
    text = "报告期营业收入稳定增长毛利率小幅上行。" * 40
    chunks = chunk_dense_block(text, chunk_size=100, overlap=20)
    assert len(chunks) >= 2
    assert chunks[1].startswith(chunks[0][-20:])


def test_chunk_dense_block_hard_splits_long_sentence() -> None:
    text = "研" * 1000  # 无标点单句超长
    chunks = chunk_dense_block(text, chunk_size=100, overlap=10)
    assert len(chunks) >= 10
    assert "".join(chunks) == text  # 切开拼回不漏


def test_chunk_blocks_cuts_at_block_boundary_and_overlaps() -> None:
    b1 = _para("研" * 80)
    b2 = _para("发" * 80)
    b3 = _para("投" * 80)
    b4 = _para("入" * 80)
    pairs = chunk_blocks([b1, b2, b3, b4], chunk_size=200, overlap=20)
    texts = [p[0] for p in pairs]
    # 首个 chunk 由完整块拼成（块边界切分，不拆块）
    assert texts[0] == ("研" * 80) + ("发" * 80)
    # 相邻 chunk 恒有 overlap
    assert texts[1].startswith(texts[0][-20:])


def test_chunk_blocks_splits_oversized_block() -> None:
    big = _para("研" * 900)  # 单个块超 chunk_size
    pairs = chunk_blocks([big], chunk_size=200, overlap=10)
    assert any(len(p[0]) >= 200 for p in pairs)
    assert len(pairs) >= 2


def test_chunk_blocks_table_one_chunk() -> None:
    tbl = Block(block_type="table", section_path=["3.2 研发投入"], page_start=42, page_end=42, text="表 12 | 项目 | 金额 | 投入 | 100", table_headers=["项目", "金额"], table_caption="表 12")
    pairs = chunk_blocks([tbl], chunk_size=400, overlap=80)
    assert len(pairs) == 1
    assert pairs[0][1].block_type == "table"


def test_chunk_section_truncates() -> None:
    sec = SectionNode(id="s1", heading="3.2 研发投入", path=["第三节", "3.2 研发投入"], blocks=[_para("报告期内研发投入增长。")], text="")
    assert chunk_section(sec, max_chars=10) == "报告期内研发投入增长。"[:10]
