"""AI算力产业链高频跟踪快报 —— 确定性模板渲染器单测（无 LLM、无网络）。

覆盖：六段结构渲染、字段别名提取、业绩预告异动阈值(>50% 或 <-20%)、规则一句话研判、空证据/业务信封容错降级。
"""

from __future__ import annotations

import json
from typing import Any

from demomcp.graph.tracker_render import render_tracker


def _ev(source: str, data: Any) -> dict[str, Any]:
    """构造一条 tool 证据：source=接口名，content=JSON 文本。"""
    return {"source_type": "tool", "source": source, "content": json.dumps(data, ensure_ascii=False)}


def _ctx() -> dict[str, Any]:
    return {"query": "生成 AI 算力产业链今日跟踪快报", "date": "2026-08-14"}


def _full_evidence() -> list[dict[str, Any]]:
    return [
        _ev("ths_daily", {"code": 0, "data": [{"name": "光模块(CPO)", "pct_change": 2.78}]}),
        _ev("moneyflow", {"code": 0, "data": [
            {"name": "新易盛", "net_amount": 349200.0},
            {"name": "天孚通信", "net_amount": 228000.0},
        ]}),
        _ev("daily", {"code": 0, "data": [{"ts_code": "300502.SZ", "name": "新易盛", "pct_change": 4.5, "close": 448.08, "week_pct": 6.44}]}),
        _ev("announcement", {"code": 0, "data": [{"name": "新易盛", "ann_type": "业绩预告", "content": "预计上半年归母净利70-80亿", "ann_date": "2026-08-14"}]}),
        _ev("forecast", {"code": 0, "data": [{"name": "新易盛", "net_profit_yoy": 102.93, "reason": "AI算力投资增长"}]}),
        _ev("news", {"code": 0, "data": [{"title": "英伟达Spectrum-X进入量产阶段"}]}),
    ]


def test_tracker_render_six_sections() -> None:
    """完整证据 → 六段标题齐全；板块行情、TOP5 资金流、标的行情、业绩预告异动、催化均渲染。"""
    md = render_tracker(_full_evidence(), _ctx())
    for sec in ("一、板块概览", "二、标的池行情速览", "三、关键公告", "四、业绩预告异动提示", "五、产业链催化事件", "六、一句话研判"):
        assert sec in md, f"缺 {sec}"
    assert "AI算力产业链" in md  # 板块名保留「产业链」
    assert "光模块(CPO)" in md and "+2.78%" in md
    assert "本周资金净流入 TOP5" in md and "34.92" in md  # 349200万 → 34.92亿
    assert "新易盛" in md and "448.08元" in md and "+6.44%" in md
    assert "业绩预告" in md and "+102.93%" in md  # >50 触发
    assert "英伟达Spectrum-X" in md


def test_tracker_render_forecast_threshold() -> None:
    """业绩预告异动：同比 >50 或 <-20 才列；二者之间不触发。"""
    ev = [
        _ev("forecast", {"code": 0, "data": [
            {"name": "A", "net_profit_yoy": 102.93},
            {"name": "B", "net_profit_yoy": 30.0},
            {"name": "C", "net_profit_yoy": -25.0},
        ]}),
    ]
    md = render_tracker(ev, _ctx())
    assert "+102.93%" in md and "-25.00%" in md
    assert "+30.00%" not in md  # 未触发阈值
    assert "本期无异常" not in md  # 有触发行，不是“无异常”


def test_tracker_render_rule_brief() -> None:
    """一句话研判由规则拼接（非 LLM）：含资金净流入居前 + 业绩预告触发异动。"""
    md = render_tracker(_full_evidence(), _ctx())
    assert "资金净流入居前" in md
    assert "新易盛" in md
    assert "业绩预告触发异动" in md


def test_tracker_render_empty_and_envelope_degrade() -> None:
    """空证据 / 业务信封（code!=0 无 data）→ 各段标『未接入/本期无』，一句话研判数据不足，不抛异常。"""
    # 业务信封降级：code!=0、无 data
    ev = [_ev("moneyflow", {"code": 9001, "msg": "无权限", "row_count": 0, "data": []})]
    md = render_tracker(ev, _ctx())
    assert "数据未接入" in md or "本期无" in md
    assert "数据不足" in md  # 一句话研判
    # 完全空
    md2 = render_tracker([], _ctx())
    assert "数据未接入" in md2 and "标的池未接入" in md2
    assert "本期无关键公告" in md2 and "本期无异常" in md2 and "本期无明显催化" in md2
    assert "数据不足，无法研判。" in md2
