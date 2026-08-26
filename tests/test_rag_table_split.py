"""表格切块：题注识别、表头剥离、行分块、跨页表头去重。"""

from __future__ import annotations

from demomcp.rag.schemas import TableRegion
from demomcp.rag.table_split import (
    detect_header,
    header_fingerprint,
    is_caption_line,
    merge_cross_page,
    split_table_rows,
    textify_table,
)


def test_is_caption_line() -> None:
    assert is_caption_line("表 12 研发投入情况")
    assert is_caption_line("单位：万元")
    assert is_caption_line("数据来源：公司年报")
    assert not is_caption_line("研发投入情况")


def test_detect_header() -> None:
    rows = [["项目", "本报告期"], ["营业收入", "100"], ["净利润", "20"]]
    headers, data = detect_header(rows)
    assert headers == ["项目", "本报告期"]
    assert data == [["营业收入", "100"], ["净利润", "20"]]


def test_textify_table() -> None:
    assert textify_table(["项目", "金额"], ["营收", "100"]) == "项目 | 金额 | 营收 | 100"


def test_textify_table_coerces_none_and_merged_cells() -> None:
    """PyMuPDF extract() 可能返回 None 或合并单元格（嵌套 list），须兜底为字符串。"""
    assert textify_table(["项目", "金额"], ["营收", None]) == "项目 | 金额 | 营收 | "
    assert textify_table(["项目", "金额"], ["合并", ["A", "B"]]) == "项目 | 金额 | 合并 | A B"


def test_detect_header_coerces_cells() -> None:
    headers, data = detect_header([["项目", None], ["营收", "100"]])
    assert headers == ["项目", ""]
    assert data == [["营收", "100"]]


def test_split_table_rows_single_block_if_small() -> None:
    rows = [["营收", "100"], ["净利", "20"]]
    blocks = split_table_rows(rows, caption="表 1 经营简况", headers=["项目", "金额"], section_path=["第三节"], page_start=3, page_end=3, rows_per_chunk=8, chunk_size=400)
    assert len(blocks) == 1
    assert blocks[0].block_type == "table"
    assert blocks[0].table_caption == "表 1 经营简况"
    assert blocks[0].table_headers == ["项目", "金额"]
    assert blocks[0].section_path == ["第三节"]
    assert blocks[0].text.startswith("表 1 经营简况")


def test_split_table_rows_chunked() -> None:
    rows = [[f"行{i}", str(i)] for i in range(10)]
    blocks = split_table_rows(rows, caption="表 2", headers=["甲", "乙"], section_path=["3.2 研发投入"], page_start=42, page_end=42, rows_per_chunk=4, chunk_size=400)
    assert len(blocks) == 3  # 4 + 4 + 2
    # 每块仍携带同一表头 + 题注
    for b in blocks:
        assert b.table_headers == ["甲", "乙"]
        assert b.table_caption == "表 2"
        assert b.page_start == 42
    assert len(blocks[0].text.split("\n")) >= 2


def test_merge_cross_page_dedupes_header_row() -> None:
    p1 = TableRegion(page=42, caption="表 3", headers=["A", "B"], rows=[["1", "2"], ["3", "4"]], top=0, left=0)
    # 续表：首行是重复表头 + 新数据
    p2 = TableRegion(page=43, caption="表 3", headers=["A", "B"], rows=[["A", "B"], ["5", "6"]], top=0, left=0)
    merged = merge_cross_page([p1, p2])
    assert len(merged) == 1
    # 重复表头行被去掉，数据行合并为 3 行
    assert merged[0].rows == [["1", "2"], ["3", "4"], ["5", "6"]]


def test_header_fingerprint_stable() -> None:
    assert header_fingerprint(["项目", "金额"]) == header_fingerprint([" 项目 ", "金额"])
    assert header_fingerprint(["项目", "金额"]) != header_fingerprint(["项目", "期数"])
