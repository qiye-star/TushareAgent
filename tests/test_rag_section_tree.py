"""章节语义树：标题检测（字号 + 正则融合、仅正则降一等）与嵌套建树。"""

from __future__ import annotations

from demomcp.rag.schemas import LayoutBlock
from demomcp.rag.section_tree import build_section_tree, detect_headings


def _blk(page: int, text: str, size: float, block_no: int) -> LayoutBlock:
    return LayoutBlock(page=page, text=text, bbox=(0, 0, 100, 100), kind="text", size=size, block_no=block_no)


def _doc_blocks() -> list[LayoutBlock]:
    """制式年报混排：正文 10.5pt 为主，标题字号递增。"""
    blocks = [
        _blk(1, "第七章 财务会计报告", 18.0, 0),
        _blk(1, "公司主要会计政策与会计估计发生变更。", 10.5, 1),
        _blk(2, "第三节 管理层讨论与分析", 16.0, 2),
        _blk(2, "公司应对外部环境变化，加强了成本管理。", 10.5, 3),
        _blk(3, "3.2 研发投入", 14.0, 4),
        _blk(3, "报告期内公司研发投入占营业收入的比例。", 10.5, 5),
        _blk(4, "一、经营情况讨论", 10.5, 6),
        _blk(4, "报告期营业收入稳定增长，毛利率小幅上行。", 10.5, 7),
    ]
    return blocks


def test_detect_headings_fusion() -> None:
    cands = detect_headings(_doc_blocks())
    got = {c.heading: c.level for c in cands}
    assert got["第七章 财务会计报告"] == 1
    assert got["第三节 管理层讨论与分析"] == 2
    assert got["3.2 研发投入"] == 3
    # 仅正则命中、字号近正文 → 降一等（4 中文序号 → 5）
    assert got["一、经营情况讨论"] == 5
    # 纯正文（无 regex、字号=正文）不识别
    assert "报告期营业收入稳定增长，毛利率小幅上行。" not in got


def test_build_section_tree_nested_path_spans() -> None:
    cands = detect_headings(_doc_blocks())
    tree = build_section_tree(cands, doc_id="fy2024_byd", total_pages=4)
    ch7 = tree.children[0]
    assert ch7.heading == "第七章 财务会计报告"
    ch3 = ch7.children[0]
    assert ch3.heading == "第三节 管理层讨论与分析"
    rnd = ch3.children[0]
    assert rnd.heading == "3.2 研发投入"
    last = rnd.children[0]
    assert last.heading == "一、经营情况讨论"
    # path 是祖先链 + 自身，末端为自身 heading
    assert last.path[-1] == "一、经营情况讨论"
    assert last.path[0] == "第七章 财务会计报告"
    # 页码跨度
    assert rnd.page_start == 3
    assert tree.page_start == 1


def test_build_section_tree_id_stable() -> None:
    cands = detect_headings(_doc_blocks())
    a = build_section_tree(cands, doc_id="fy2024_byd", total_pages=4)
    b = build_section_tree(cands, doc_id="fy2024_byd", total_pages=4)
    assert a.children[0].children[0].id == b.children[0].children[0].id


def test_detect_headings_rejects_long_enumeration_paragraph() -> None:
    """以编号开头但非大字号的长文本段落（正文）不得误判为子标题。"""
    long_body = "1 、生态破圈战（2025Q3）：借势方程豹钛7车型发布，打造智慧生态标杆案例，联动影石、好孩子、支付宝等知名品牌实现智慧生态首发落地。"
    blocks = [
        _blk(1, "第三节 管理层讨论与分析", 16.0, 0),
        _blk(1, long_body, 10.5, 1),  # 正文长度段 + 正文字号 → 非标题
        _blk(1, "3.2 研发投入", 14.0, 2),
    ]
    cands = detect_headings(blocks)
    got = {c.heading for c in cands}
    assert long_body not in got
    assert "3.2 研发投入" in got


def test_detect_headings_filters_running_header() -> None:
    """跨多页重复的「页眉/页脚标题」（如报告全称）不是真标题，应被过滤掉，避免污染章节树。"""
    header = "宁德时代新能源科技股份有限公司2025 年年度报告全文"
    blocks = [
        _blk(p, header, 18.0, p) for p in range(1, 7)  # 同一标题出现在 6 个不同页 → running header
    ]
    blocks += [
        _blk(1, "第三节 管理层讨论与分析", 16.0, 10),
        _blk(1, "3.2 研发投入", 14.0, 11),
    ]
    cands = detect_headings(blocks)
    got = {c.heading for c in cands}
    assert header not in got
    assert "第三节 管理层讨论与分析" in got
    assert "3.2 研发投入" in got


def test_detect_headings_rejects_sentence_punctuation() -> None:
    """含句读（，；。）的正文片段不是标题（如「3.0 Evo」打造，标配…」），避免污染 section_path。"""
    blocks = [
        _blk(1, "第三节 管理层讨论与分析", 16.0, 0),
        _blk(1, "3.0 Evo」打造，标配「天神之眼C」辅助驾驶系统，", 14.0, 1),
        _blk(1, "3.2 研发投入", 14.0, 2),
    ]
    got = {c.heading for c in detect_headings(blocks)}
    assert "3.2 研发投入" in got
    assert not any("天神之眼C" in h for h in got)


def test_detect_headings_skips_toc_entries_and_marker() -> None:
    """目录条目（点线导引+尾页码）与「目录」标记不得成为标题候选，避免 TOC 污染章节树。"""
    blocks = [
        _blk(1, "第三节 管理层讨论与分析", 16.0, 0),
        _blk(1, "第八节 财务报告 .................. 150", 12.0, 1),  # 目录条目（正文/近似字号）
        _blk(1, "目录", 18.0, 2),  # 目录页标记（大字号）
        _blk(1, "3.2 研发投入", 14.0, 3),
        _blk(1, "报告期研发投入情况说明。", 10.5, 4),
    ]
    cands = detect_headings(blocks)
    got = [c.heading for c in cands]
    assert "第三节 管理层讨论与分析" in got
    assert "3.2 研发投入" in got
    assert "第八节 财务报告 .................. 150" not in got
    assert "目录" not in got
    assert "报告期研发投入情况说明。" not in got
