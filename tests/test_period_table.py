"""多期财务数据确定性表格化测试：_period_label / _dedupe_period_rows / _render_period_table。

背景（线上真实事故）：证据摘要按字符位置截断时会把「用户问的报告期」折进匿名的
「中间省略 N 行」提示里，模型收到「数据确实取到了、只是没给你看」的暗示后，
用视野内邻近可见的报告期数值顶替、换个标签充数——同一份宁德时代 fina_indicator
数据，2025H1/2025Q1 被填成了 2024H1/2024Q1 的真实数值（用户用 Wind 数据交叉核对发现）。
本文件用真实抓取到的那份数据做回归 fixture，不是凭空编造。
"""

from __future__ import annotations

from demomcp.graph.nodes import (
    _MAX_EVIDENCE_CHARS,
    _dedupe_period_rows,
    _period_label,
    _render_period_table,
)

# 真实数据：宁德时代（300750.SZ）fina_indicator 10 期，线上事故复现用例的原始返回
# （2026-09-10 会话 4bb1229e3dbb 的真实工具返回，直接摘自 demo.db）。
CATL_FINA_INDICATOR_ROWS = [
    {"ts_code": "300750.SZ", "end_date": "20260630", "ann_date": "20260725", "grossprofit_margin": 23.9284, "netprofit_margin": 16.9837, "or_yoy": 54.8004, "netprofit_yoy": 41.9839, "roe_waa": 12.08},
    {"ts_code": "300750.SZ", "end_date": "20260331", "ann_date": "20260416", "grossprofit_margin": 24.8156, "netprofit_margin": 17.6079, "or_yoy": 52.4487, "netprofit_yoy": 48.5237, "roe_waa": 5.98},
    {"ts_code": "300750.SZ", "end_date": "20251231", "ann_date": "20260310", "grossprofit_margin": 26.2728, "netprofit_margin": 18.1227, "or_yoy": 17.0406, "netprofit_yoy": 42.2834, "roe_waa": 24.91},
    {"ts_code": "300750.SZ", "end_date": "20250930", "ann_date": "20251021", "grossprofit_margin": 25.3098, "netprofit_margin": 18.4748, "or_yoy": 9.2753, "netprofit_yoy": 36.2018, "roe_waa": 17.76},
    {"ts_code": "300750.SZ", "end_date": "20250630", "ann_date": "20250731", "grossprofit_margin": 25.023, "netprofit_margin": 18.0928, "or_yoy": 7.2673, "netprofit_yoy": 33.3267, "roe_waa": 11.63},
    {"ts_code": "300750.SZ", "end_date": "20250331", "ann_date": "20250415", "grossprofit_margin": 24.4077, "netprofit_margin": 17.5453, "or_yoy": 6.185, "netprofit_yoy": 32.8512, "roe_waa": 5.49},
    {"ts_code": "300750.SZ", "end_date": "20241231", "ann_date": "20250315", "grossprofit_margin": 24.4449, "netprofit_margin": 14.9185, "or_yoy": -9.7039, "netprofit_yoy": 15.0119, "roe_waa": 24.13},
    {"ts_code": "300750.SZ", "end_date": "20240930", "ann_date": "20241019", "grossprofit_margin": 28.185, "netprofit_margin": 14.9523, "or_yoy": -12.092, "netprofit_yoy": 15.5901, "roe_waa": 17.73},
    {"ts_code": "300750.SZ", "end_date": "20240630", "ann_date": "20240727", "grossprofit_margin": 26.5334, "netprofit_margin": 14.9183, "or_yoy": -11.8783, "netprofit_yoy": 10.3668, "roe_waa": 11.39},
    {"ts_code": "300750.SZ", "end_date": "20240331", "ann_date": "20240416", "grossprofit_margin": 26.4155, "netprofit_margin": 14.0348, "or_yoy": -10.4086, "netprofit_yoy": 7.001, "roe_waa": 5.18},
]

# 真实数据：同一会话 income 接口返回的近似重复行（同 end_date+report_type，仅 rd_exp 有无差异）。
CATL_INCOME_DUP_PAIR = [
    {"ts_code": "300750.SZ", "end_date": "20250331", "ann_date": "20250415", "report_type": "1",
     "revenue": 84704589000.0, "rd_exp": None, "n_income_attr_p": 13962558000.0},
    {"ts_code": "300750.SZ", "end_date": "20250331", "ann_date": "20250415", "report_type": "1",
     "revenue": 84704589000.0, "rd_exp": 4814003000.0, "n_income_attr_p": 13962558000.0},
]


def test_period_label_standard_mmdd() -> None:
    assert _period_label("20250331") == "2025Q1"
    assert _period_label("20250630") == "2025H1"
    assert _period_label("20250930") == "2025前三季度"
    assert _period_label("20251231") == "2025年报"


def test_period_label_nonstandard_falls_back_to_raw() -> None:
    assert _period_label("20250815") == "20250815"  # 非标准 MM-DD，原样返回不臆测
    assert _period_label(None) == "None"
    assert _period_label(20250630) == "20250630"  # 非字符串（如整数）同样走兜底


def test_dedupe_collapses_exact_duplicate_rows() -> None:
    rows = [CATL_FINA_INDICATOR_ROWS[0], dict(CATL_FINA_INDICATOR_ROWS[0]), CATL_FINA_INDICATOR_ROWS[1]]
    out = _dedupe_period_rows(rows)
    assert len(out) == 2


def test_dedupe_keeps_more_complete_row_for_same_period() -> None:
    """真实近似重复行样本：同 end_date+report_type，一条 rd_exp=None 一条有值——保留更完整的那条。"""
    out = _dedupe_period_rows(CATL_INCOME_DUP_PAIR)
    assert len(out) == 1
    assert out[0]["rd_exp"] == 4814003000.0


def test_dedupe_does_not_merge_different_report_type() -> None:
    """report_type 不同代表真实不同口径（如合并报表 vs 调整前），不能被当成重复行合并掉。"""
    a = {"end_date": "20250331", "report_type": "1", "revenue": 100.0}
    b = {"end_date": "20250331", "report_type": "4", "revenue": 90.0}
    out = _dedupe_period_rows([a, b])
    assert len(out) == 2


def test_render_period_table_fits_all_periods_without_truncation() -> None:
    """回归本次事故：真实 10 期数据在 2000 字符预算内应该全部展示，不需要截断——
    原字符位置截断在同样预算下只能露出 4 期（恰好漏掉 2025H1/2025Q1）。"""
    table = _render_period_table(CATL_FINA_INDICATOR_ROWS, _MAX_EVIDENCE_CHARS)
    assert len(table) <= _MAX_EVIDENCE_CHARS
    assert "中间省略" not in table
    assert "未展示" not in table
    for row in CATL_FINA_INDICATOR_ROWS:
        assert _period_label(row["end_date"]) in table
    assert "2025H1" in table and "2025Q1" in table  # 用户实际问到、曾被错误顶替的两期
    assert table.count("300750.SZ") == 1  # ts_code 提到表头外，不逐行重复


def test_render_period_table_truncates_by_whole_period_and_names_dropped() -> None:
    """超预算时按整期裁剪：保留最近若干期整行，被裁掉的期次按标签点名，不切穿任何一行。

    用 10 年季度历史（40 期，贴近真实查询规模——prompt 里报告期默认引导是「近两年」，
    十年已属超长区间）而非几十年极端历史：省略提示按标签逐个点名，行数一多提示本身也会变长，
    这是设计里明确接受的「软上限」（跟既有 _truncate_rows_head_tail 的省略提示同一约定），
    不该用极端规模去卡一个没约束过的硬边界。"""
    # 字段宽度贴近真实 fina_indicator（约 8 个数值字段），40 期在这个宽度下必然超 2000 字符预算。
    rows = [
        {
            "ts_code": "688256.SH", "end_date": f"{y}{md}", "ann_date": f"{y}{md}",
            "grossprofit_margin": 50.0 + y % 10, "netprofit_margin": 30.0, "roe_waa": 12.0,
            "or_yoy": 20.0, "netprofit_yoy": 15.0, "debt_to_assets": 40.0,
        }
        for y in range(2016, 2026)
        for md in ("0331", "0630", "0930", "1231")
    ]
    limit = _MAX_EVIDENCE_CHARS
    table = _render_period_table(rows, limit)
    assert len(rows) == 40
    assert "未展示" in table  # 40 期在该字段规模下超预算，确实触发了截断
    assert "Q1" in table and "H1" in table  # 保留的是最近若干期
    # 没有任何一行被从中间切断：每条数据行的列数应与表头一致
    lines = [ln for ln in table.split("\n") if ln.startswith(("| 20", "| 19"))]
    header_cols = table.split("\n")[1].count("|")
    for ln in lines:
        assert ln.count("|") == header_cols
