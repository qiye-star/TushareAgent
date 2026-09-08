"""quickreport 确定性计算层：六段投影 + 三源合并 + 阈值过滤 + 一句话研判。"""

from __future__ import annotations

import pytest

from demomcp.quickreport.projection import (
    _fmt_range,
    build_brief,
    flow_to_yi,
    join_watchlist,
    pool_stats,
    project_announce,
    project_board,
    project_forecast,
    project_news,
)

NAMES = {"300502.SZ": "新易盛", "300394.SZ": "天孚通信"}


def _daily_row(code, close, pct_chg=None, trade_date="20260904"):
    row = {"ts_code": code, "close": close, "trade_date": trade_date}
    if pct_chg is not None:
        row["pct_chg"] = pct_chg
    return row


# —— board ——


def test_project_board_fields_and_top_inflow() -> None:
    rows = [
        {"name": "光模块(CPO)", "pct_change": 2.78, "net_mf_amount": 120000, "备注": "量产催化", "provider": "ths"},
        {"name": "液冷服务器", "pct_change": -1.2, "net_mf_amount": 30000, "provider": "ths"},
        {"name": "算力租赁", "pct_change": 0.5, "net_mf_amount": None, "provider": "ths"},
    ]
    out = project_board(rows)
    assert out["rows"][0]["name"] == "光模块(CPO)"
    assert out["rows"][0]["pct"] == 2.78 and out["rows"][0]["pct_text"] == "+2.78%"
    assert out["rows"][0]["inflow"] == 12.0 and out["rows"][0]["inflow_text"] == "12.00"  # 万元→亿
    assert out["rows"][1]["pct_text"] == "-1.20%"
    assert out["rows"][2]["inflow"] is None and out["rows"][2]["inflow_text"] is None
    # TOP5 按净流入降序
    assert [x["name"] for x in out["top_inflow"]] == ["光模块(CPO)", "液冷服务器"]
    assert out["top_inflow"][0]["rank"] == 1


def test_project_board_ignores_empty_name_rows() -> None:
    out = project_board([{"pct_change": 1.0}, {"name": "光模块(CPO)", "pct_change": 2.0}])
    assert len(out["rows"]) == 1


def test_project_board_flow_unit_wan_to_yi() -> None:
    """板块资金流单位换算：万元 → 亿明确 /1e4；小额（<1000 万）不被启发式误判（993.99 万 ≠ 993.99 亿）。"""
    out = project_board([
        {"name": "光模块(CPO)", "pct_change": 2.0, "main_net_inflow": 993.99},        # 993.99 万 → 0.0994 亿
        {"name": "液冷服务器", "pct_change": 1.0, "main_net_inflow": 120000},        # 12 万万元? → 12 亿
        {"name": "算力租赁", "pct_change": 0.5, "main_net_inflow": 99999999},        # >100 亿 → 弃
    ])
    assert out["rows"][0]["inflow"] == pytest.approx(0.0994, abs=1e-4)
    assert out["rows"][0]["inflow_text"] == "0.10"  # .2f
    assert out["rows"][1]["inflow"] == 12.0
    assert out["rows"][2]["inflow"] is None


# —— watchlist 三源合并 ——


def test_join_watchlist_merges_three_sources() -> None:
    daily = [_daily_row("300502.SZ", 100.0, 5.67), _daily_row("300394.SZ", 50.0, -1.0)]
    prev = [_daily_row("300502.SZ", 91.75, trade_date="20260828"), _daily_row("300394.SZ", 51.0)]
    basic = [
        {"ts_code": "300502.SZ", "turnover_rate": 4.5, "total_mv": 6953000},
        {"ts_code": "300394.SZ", "turnover_rate": 2.1, "total_mv": 3000000},
    ]
    out = join_watchlist(daily, prev, basic, NAMES, display_limit=100)
    assert out["pool_count"] == 2 and out["row_count"] == 2 and out["missing_codes"] == 0
    first = out["rows"][0]
    assert first["name"] == "新易盛" and first["ts_code"] == "300502.SZ"
    assert first["pct"] == 5.67 and first["pct_text"] == "+5.67%"
    assert first["close"] == 100.0 and first["close_text"] == "100.00元"
    # week_pct = (100-91.75)/91.75*100 ≈ 8.99
    assert first["week_pct"] == pytest.approx(8.991, abs=0.01)
    assert first["week_pct_text"].startswith("+")
    assert first["turnover"] == 4.5
    assert first["market_cap"] == 695.3  # 6953000 万元 → 亿
    second = out["rows"][1]
    assert second["pct_text"] == "-1.00%"


def test_join_watchlist_missing_codes_and_truncate() -> None:
    daily = [_daily_row("300502.SZ", 100.0, 1.0)]
    out = join_watchlist(daily, [], [], NAMES, display_limit=1)
    assert out["row_count"] == 1 and out["missing_codes"] == 1
    assert out["truncated"] is False  # 1 行 ≤ limit 1
    out2 = join_watchlist(daily + [_daily_row("300394.SZ", 20.0)], [], [], NAMES, display_limit=1)
    assert out2["truncated"] is True and out2["total_count"] == 2 and len(out2["rows"]) == 1


def test_join_watchlist_week_pct_missing_prev() -> None:
    out = join_watchlist([_daily_row("300502.SZ", 100.0)], [], [], NAMES)
    assert out["rows"][0]["week_pct"] is None and out["rows"][0]["week_pct_text"] is None


# —— announce ——


def test_project_announce_dedup_sort_and_type_classification() -> None:
    rows = [
        {"ts_code": "300502.SZ", "ann_date": "20260904", "title": "2026年前三季度业绩预告"},
        {"ts_code": "300394.SZ", "ann_date": "20260903", "title": "关于回购公司股份的公告"},
        {"ts_code": "300502.SZ", "ann_date": "20260902", "title": "关于减持计划的公告"},
        {"ts_code": "600000.SH", "ann_date": "20260904", "title": "关于公司经营情况的公告"},  # 不在池里 → name 走别名
    ]
    out = project_announce(rows, NAMES)
    assert [i["ann_date"] for i in out["items"]] == ["20260904", "20260904", "20260903", "20260902"]
    types = {i["title"]: i["type"] for i in out["items"]}
    assert types["2026年前三季度业绩预告"] == "业绩预告"
    assert types["关于回购公司股份的公告"] == "回购"
    assert types["关于减持计划的公告"] == "股东增减持"
    assert types["关于公司经营情况的公告"] == "其他"
    names = {i["title"]: i["name"] for i in out["items"]}
    assert names["2026年前三季度业绩预告"] == "新易盛"
    assert names["关于公司经营情况的公告"] == "600000.SH"  # 池外标的：code 兜底当 name
    # limit 截断
    assert len(project_announce(rows[:2], NAMES, limit=1)["items"]) == 1


def test_project_announce_prefers_native_type_field() -> None:
    out = project_announce(
        [{"ts_code": "300502.SZ", "ann_date": "20260904", "title": "有啥说啥", "type": "业绩预告"}], NAMES
    )
    assert out["items"][0]["type"] == "业绩预告"


# —— forecast 阈值 ——


def test_project_forecast_threshold_filter_and_alias_gap() -> None:
    """守住第四段恒「无异常」：p_change_min/max（forecast）与 yoy_net_profit（express）都能触发。"""
    rows = [
        {"ts_code": "300502.SZ", "period": "20260930", "p_change_min": 60.0, "p_change_max": 110.0,
         "change_reason": "算力需求高增"},
        {"ts_code": "300394.SZ", "period": "20260930", "p_change_min": 30.0, "p_change_max": 40.0},
        {"ts_code": "300017.SZ", "period": "20260930", "p_change_min": -25.0, "p_change_max": -10.0},
        {"ts_code": "300308.SZ", "end_date": "20260630", "yoy_net_profit": 102.93},
        {"ts_code": "300308.SZ", "end_date": "20260630", "yoy_net_profit": 30.0},  # 不触发
    ]
    out = project_forecast(rows, NAMES, up=50.0, down=-20.0)
    assert out["hit_count"] == 3
    items = out["items"]
    assert items[0]["name"] == "新易盛" and items[0]["scope"] == "20260930"
    assert (items[0]["yoy_min"], items[0]["yoy_max"]) == (60.0, 110.0)
    assert items[0]["yoy_text"] == "60.00% ~ 110.00%"
    assert items[0]["hits"] == ["up"]
    assert items[0]["reason"] == "算力需求高增"
    assert items[1]["name"] == "300017.SZ" and items[1]["hits"] == ["down"]  # 池外 code 兜底
    assert items[2]["name"] == "300308.SZ" and items[2]["yoy_text"] == "102.93%"  # express 单值
    # 30%~40% 不触发；30% 单值不触发 → 不在 items


def test_fmt_range() -> None:
    assert _fmt_range(60.0, 110.0) == "60.00% ~ 110.00%"
    assert _fmt_range(102.93, 102.93) == "102.93%"
    assert _fmt_range(None, 80.0) == "80.00%"
    assert _fmt_range(None, None) == "—"


# —— news ——


def test_project_news_fields() -> None:
    rows = [
        {
            "title": "英伟达宣布量产",
            "src": "wallstreetcn",
            "datetime": "2026-09-04 09:30:00",
            "url": "https://example.com/news/1",
        },
        {"title": "无来源的新闻", "content": "x"},
        {"title": "", "src": "sina"},
    ]
    out = project_news(rows)
    assert len(out["items"]) == 2
    assert out["items"][0] == {
        "title": "英伟达宣布量产",
        "src": "wallstreetcn",
        "datetime": "2026-09-04 09:30:00",
        "url": "https://example.com/news/1",
    }
    assert out["items"][1]["src"] == "" and out["items"][1]["datetime"] == "" and out["items"][1]["url"] == ""


# —— brief ——


def test_build_brief_rule_order_and_150_limit() -> None:
    board = [{"name": "光模块(CPO)", "pct_text": "+2.78%"}]
    inflow = [{"name": "新易盛", "inflow_text": "3.21"}]
    forecast = [{"name": "新易盛"}]
    out = build_brief(board, inflow, forecast)
    assert "光模块(CPO)（+2.78%）" in out["text"]  # 新文案无「板块当日涨跌：」前缀（精炼）
    assert "资金净流入居前：新易盛（3.21亿）" in out["text"]
    assert "新易盛 业绩预告触发异动" in out["text"]
    assert out["chars"] <= 150
    # 无素材
    assert build_brief([], [], []) == {"text": "数据不足，无法研判。", "chars": 9}
    # 长素材截断 150
    long = build_brief([{"name": "板块A超长名称" * 30, "pct_text": "+2.78%"}], [], [])
    assert long["chars"] == 150


def test_build_brief_extended_material() -> None:
    """素材扩充：池内涨跌统计（数值排序不被字符串坑）+ 涨幅居前 TOP2 + 公告/催化计数。"""
    board = [{"name": "沪深300", "pct_text": "+0.85%"}]
    inflow = [{"name": "中际旭创", "inflow_text": "17.91"}, {"name": "新易盛", "inflow_text": "8.20"}]
    forecast = [{"name": "康强科技", "yoy_text": "-55.31% ~ -50.34%"}]
    watch = [
        {"name": "蓝色光标", "pct": 5.91, "pct_text": "+5.91%"},
        {"name": "宇信科技", "pct": 5.27, "pct_text": "+5.27%"},
        {"name": "浪潮信息", "pct": -9.99, "pct_text": "-9.99%"},
        {"name": "奥飞数据", "pct": 0.0, "pct_text": "+0.00%"},
        {"name": "深科技", "pct": -2.21, "pct_text": "-2.21%"},
    ]
    out = build_brief(board, inflow, forecast, watch_rows=watch, counts={"announce": 3, "news": 5})
    assert "沪深300（+0.85%）" in out["text"]
    assert "资金净流入居前：中际旭创（17.91亿）、新易盛（8.20亿）" in out["text"]
    assert "标的池 2 涨 2 跌" in out["text"]  # +5.91/+5.27 涨、-9.99/-2.21 跌、0.0 平不计
    # 涨幅居前排序按数值（+5.91% > +5.27%），且 TOP2 都出现
    assert "涨幅居前：蓝色光标（+5.91%）、宇信科技（+5.27%）" in out["text"]
    assert "康强科技 业绩预告（-55.31% ~ -50.34%）触发异动" in out["text"]
    assert "公告 3 条" in out["text"] and "催化 5 条" in out["text"]
    assert 70 <= out["chars"] <= 150

    # watch/counts 缺省 → 老素材行为不变
    old = build_brief(board, inflow, forecast)
    assert "标的池" not in old["text"] and "公告" not in old["text"]


# ---------------------------------------------------------------------------
# Phase 1 回归：已实测确认的计算缺陷
# ---------------------------------------------------------------------------


def test_project_board_sorts_zero_inflow_above_negative() -> None:
    """`inflow == 0.0` 不能被当成「无数据」排到负值之后。

    原实现 `key=lambda x: (x["inflow"] or float("-inf"))` 里 0.0 是 falsy → 变 -inf，
    于是「零净流入」排在「净流出 50 亿」后面。真实的排序错误。
    """
    rows = [
        {"name": "净流出", "main_net_inflow": -500000},  # -50 亿
        {"name": "零流入", "main_net_inflow": 0},
        {"name": "净流入", "main_net_inflow": 100000},  # +10 亿
    ]
    out = project_board(rows)
    assert [x["name"] for x in out["top_inflow"]] == ["净流入", "零流入", "净流出"]
    assert out["top_inflow"][1]["inflow"] == 0.0


def test_join_watchlist_indexed_lookup_large_pool() -> None:
    """全市场快照 × 大池子：索引化后结果必须与逐行扫描完全一致。

    原实现对每个池内代码在全市场快照里做两次 `next(...)` 线性扫
    （实测 211 只 × ~5400 行 × 2 ≈ 230 万次比较）。这里只验正确性，不做计时断言（CI 会 flaky）。
    """
    market = [
        {"ts_code": f"{600000 + i}.SH", "close": 10.0 + i, "pct_change": float(i % 5) - 2}
        for i in range(3000)
    ]
    basic = [{"ts_code": f"{600000 + i}.SH", "turnover_rate": 1.5, "total_mv": 1000000} for i in range(3000)]
    prev = [{"ts_code": f"{600000 + i}.SH", "close": 10.0} for i in range(3000)]
    names = {f"{600000 + i}.SH": f"股票{i}" for i in range(0, 400, 2)}  # 200 只
    out = join_watchlist(market, prev, basic, names, display_limit=100)
    assert out["pool_count"] == 200
    assert out["row_count"] == 200
    assert len(out["rows"]) == 100  # 展示截断
    assert len(out["_all_rows"]) == 200  # 统计用全量
    assert out["missing_codes"] == 0
    first = out["rows"][0]
    assert first["ts_code"] == "600000.SH"
    assert first["close"] == 10.0
    assert first["market_cap"] == 100.0  # 1000000 万 / 1e4
    assert first["week_pct"] == 0.0  # 10.0 → 10.0


def test_join_watchlist_close_and_pct_come_from_same_row() -> None:
    """close 与 pct 必须取自同一行（原实现 close 走 last-wins、pct 走 first-wins）。"""
    market = [
        {"ts_code": "000001.SZ", "close": 11.0, "pct_change": 5.0},
        {"ts_code": "000001.SZ", "close": 99.0, "pct_change": -9.0},  # 重复行（快照不该有，但要有确定语义）
    ]
    out = join_watchlist(market, [], [], {"000001.SZ": "平安"}, display_limit=10)
    row = out["rows"][0]
    assert (row["close"], row["pct"]) == (11.0, 5.0)  # first-wins，两个字段同源


def test_pool_stats_full_pool_semantics() -> None:
    """池统计口径：只算有 pct 的行，并回传 computed_over 让前端能诚实标注。"""
    rows = [
        {"pct": 3.0}, {"pct": -1.0}, {"pct": 0.0}, {"pct": None}, {"pct": 5.0},
    ]
    st = pool_stats(rows)
    assert (st["up"], st["down"], st["flat"]) == (2, 1, 1)
    assert st["computed_over"] == 4  # None 不参与
    assert st["avg_pct"] == round((3.0 - 1.0 + 0.0 + 5.0) / 4, 4)
    assert st["median_pct"] == 1.5  # sorted=[-1,0,3,5] → (0+3)/2
    assert pool_stats([])["computed_over"] == 0
    assert pool_stats([{"pct": None}])["avg_pct"] is None


def test_flow_to_yi_is_single_implementation() -> None:
    """万元→亿元换算全仓只有一份（结构性防回归：曾经 pipeline 与 projection 各有一份）。"""
    from demomcp.quickreport import pipeline as pl

    assert pl.flow_to_yi is flow_to_yi


def test_flow_to_yi_max_yi_guard_is_configurable() -> None:
    assert flow_to_yi(120000) == 12.0  # 12 亿，默认 100 亿上界内
    assert flow_to_yi(993.99) == 0.0994  # 小额不被启发式误当亿元
    assert flow_to_yi(99999999) is None  # 超 100 亿 → 单位错乱，弃
    assert flow_to_yi(600000, max_yi=50.0) is None  # 显式收紧上界时才拦
    assert flow_to_yi(400000, max_yi=50.0) == 40.0
    # 默认上界必须留到 100 亿：实测 65.48 亿（中际旭创 2026-09-07）是真实的单票净流入，
    # 收到 50 亿会把它当脏数据丢掉。
    assert flow_to_yi(654819) == 65.4819
