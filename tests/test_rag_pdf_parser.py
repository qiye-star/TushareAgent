"""PDF 解析：layout_from_synthetic 块/表结构保留；extract_doc_bits 抽取公司/年份/代码。"""

from __future__ import annotations

from demomcp.rag.pdf_parser import extract_doc_bits, layout_from_synthetic


def test_layout_from_synthetic_preserves_blocks_and_tables() -> None:
    result = layout_from_synthetic(
        blocks=[
            {"page": 1, "text": "第七章 财务会计报告", "size": 18.0, "kind": "text", "block_no": 0, "bbox": (0, 0, 100, 10)},
            {"page": 1, "text": "公司主要会计政策与估计。", "size": 10.5, "kind": "text", "block_no": 1, "bbox": (0, 10, 100, 20)},
            {"page": 2, "text": "", "size": 0.0, "kind": "image", "block_no": 2, "bbox": (0, 30, 100, 80)},
        ],
        tables=[
            {"page": 3, "caption": "表 12 研发投入情况", "headers": ["项目", "金额"], "rows": [["营收", "100"]], "top": 50.0, "left": 10.0},
        ],
        total_pages=3,
    )
    assert result.total_pages == 3
    assert len(result.blocks) == 3
    assert result.blocks[0].kind == "text"
    assert result.blocks[0].size == 18.0
    assert result.blocks[2].kind == "image"
    assert len(result.tables) == 1
    assert result.tables[0].caption == "表 12 研发投入情况"
    assert result.tables[0].headers == ["项目", "金额"]
    assert result.tables[0].rows == [["营收", "100"]]


def test_extract_doc_bits_from_text() -> None:
    result = layout_from_synthetic(
        blocks=[{"page": 1, "text": "002594.SZ 2024 年年度报告 比亚迪股份有限公司", "size": 12.0, "kind": "text", "block_no": 0, "bbox": (0, 0, 100, 20)}],
        tables=[], total_pages=1,
    )
    bits = extract_doc_bits(result, company_map={"比亚迪": "比亚迪"})
    assert bits["company_code"] == "002594.SZ"
    assert bits["year"] == 2024
    assert bits["company"] == "比亚迪"
