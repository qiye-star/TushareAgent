"""确定性计算层：原始工具行（行业 dict）→ 六段 records（数字/文本双份；null 语义保留，前端渲染 '—'）。

复用 `demomcp.graph.tracker_render` 的无状态纯函数与字段别名表（**只 import，文件零改动**——
该模块的 `render_tracker`/`_section_*` 被 tests/test_tracker_render.py 锁住 Markdown 形态，不可触碰；
这里消费的是无测试锁定的解析/格式化辅助，未来即使并发会话改动该文件也只影响 Markdown 层）。

`build_brief` 此处自持（与 tracker_render._brief 语义一致、各自演进）——属于段落文案层，
自持保证快报输出不被 tracker skill 的改动静默传导。
"""

from __future__ import annotations

from typing import Any

from demomcp.graph.tracker_render import (
    _ANN_CONTENT,
    _ANN_DATE,
    _ANN_TYPE,
    _CLOSE,
    _FOR_REASON,
    _INFLOW,
    _NEWS,
    _PCT,
    _REMARK,
    _fmt_pct,
    _fmt_price,
    _get,
    _name_of,
    _num,
    _text,
    _to_yi,
)
from demomcp.quickreport.predicate import (
    news_meta_of,
    news_url_of,
    range_hit,
    scope_of,
    yoy_of,
)

# 公告标题 → 类型分类（anns_d 的 type 列未证实存在，按标题关键词兜底分类）
_ANN_TYPE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("业绩预告", "业绩预增", "业绩预减", "业绩预测"), "业绩预告"),
    (("业绩快报",), "业绩快报"),
    (("减持", "增持", "持股计划", "权益变动"), "股东增减持"),
    (("回购",), "回购"),
    (("募投", "募集资金", "定增", "非公开发行", "可转债"), "募投融资"),
    (("中标", "重大合同", "战略合作", "框架协议"), "重大合同"),
    (("分红", "利润分配", "权益分派"), "分红"),
)


# ---------------------------------------------------------------------------
# 字段投影（每段）
# ---------------------------------------------------------------------------

def _code_of(row: dict[str, Any]) -> str:
    return _text(_get(row, "ts_code"))


def _pct_proj(row: dict[str, Any]) -> dict[str, Any]:
    pct = _num(_get(row, *_PCT))
    return {"pct": pct, "pct_text": _fmt_pct(pct) if pct is not None else None}


def flow_to_yi(v: Any, *, max_yi: float = 100.0) -> float | None:
    """资金流净额（**万元**）→ 亿元：/1e4。**全模块唯一一份换算**（pipeline 也 import 它）。

    不用 tracker_render._yi_num 的「>=1000 视为万元」启发式——小额流入会被误当亿元，
    量级错 1 万倍（实测：光庭信息净流入 993.99 万 → 误报 993.99 亿）；管线数据源自控，明确换算。

    `max_yi` 是**单条**记录的合理性上界（默认 100 亿）：超界视为单位错乱而丢弃，
    宁可标未接入也不误导。这是逐条守卫——池级合计刻意不设上界（丢掉真实的大额净流入
    比标出来更糟），合计的可疑判定见 `pipeline._pool_inflow_sum` 的 `suspect` 字段。
    """
    n = _num(v)
    if n is None:
        return None
    if abs(n) > max_yi * 1e4:
        return None
    return round(n / 1e4, 4)


_flow_yi = flow_to_yi  # 模块内既有调用点沿用短名


def project_board(rows: list[dict[str, Any]], limit: int = 20) -> dict[str, Any]:
    """板块/概念行投影：name/pct/inflow/remark/provider。

    主行保序（pipeline 已按来源排）；资金净流入非空的另成 top_inflow（按金额降序取 TOP5）。
    """
    out: list[dict[str, Any]] = []
    inflow_rows: list[dict[str, Any]] = []
    for r in rows:
        name = _name_of(r)
        if not name:
            continue
        inflow = _flow_yi(_get(r, *_INFLOW))
        row = {
            "name": name,
            **(_pct_proj(r)),
            "inflow": inflow,
            "inflow_text": _to_yi(inflow) if inflow is not None else None,
            "remark": _text(_get(r, *_REMARK)),
            "provider": _text(r.get("provider")),
        }
        out.append(row)
        if inflow is not None:
            inflow_rows.append(row)
    # 只有非 None 的行会进 inflow_rows（上面有 `if inflow is not None` 守卫），直接取值排序。
    # 原写法 `x["inflow"] or float("-inf")` 会把 **0.0**（falsy）当成 -inf，
    # 排到 -50 亿之后——真实的排序错误。
    inflow_rows.sort(key=lambda x: x["inflow"], reverse=True)
    top_inflow = [
        {"rank": i + 1, "name": x["name"], "inflow": x["inflow"], "inflow_text": x["inflow_text"]}
        for i, x in enumerate(inflow_rows[:5])
    ]
    return {"rows": out[:limit], "top_inflow": top_inflow}


def join_watchlist(
    daily_rows: list[dict[str, Any]],
    prev_rows: list[dict[str, Any]],
    basic_rows: list[dict[str, Any]],
    names_by_code: dict[str, str],
    display_limit: int = 100,
) -> dict[str, Any]:
    """按 ts_code 合并三份「全市场快照」行（T-1 daily / T-6 daily / T-1 daily_basic）→ 标的池行情行。

    - pct/close 来自 T-1 daily；week_pct = (close_t - close_t5)/close_t5*100（T-1 与 T-6 交易日收盘）；
    - turnover_rate 来自 daily_basic；market_cap = total_mv(万元) / 1e4 → 亿元；
    - 池子里在 daily_rows 无行的计为 missing_codes（诚实透传，不编造）。

    返回里 `rows` 按 `display_limit` 截断供前端展示，另回传 `_all_rows`（**未截断**）
    与 `stats`（全池口径统计）。前缀 `_` 的键会被 `pipeline.build_report` 剥掉、不进 JSON 契约。
    这不是可选的便利：`build_brief` 与 KPI 指标行若拿截断后的 rows 统计，
    「标的池 N 涨 M 跌」「涨幅居前」就只覆盖了池子的前 `display_limit` 只
    （实测 211 只的池子里只统计了前 100 只，且是按 watchlist.json 原始顺序而非涨跌幅排序，
    第 101~211 只里的涨停不会被提及）。

    索引优先：三份快照都是**全市场**（实测约 5400 行），逐个池内代码做 `next(...)` 线性扫
    会是 211 × 5400 × 2 ≈ 230 万次比较。先建 dict 索引（first-wins），降到一次线性 + 常数查找。
    """
    daily_by_code: dict[str, dict[str, Any]] = {}
    for r in daily_rows:
        code = _code_of(r)
        # first-wins：与原 `next(...)` 的语义一致（原 `cur` 是 last-wins、`next` 是 first-wins，
        # 两者对同一行取值本应一致——快照里代码唯一，此处统一成一份、让 close 与 pct 必然同源）
        if code in names_by_code and code not in daily_by_code:
            daily_by_code[code] = r
    prev_close_by_code: dict[str, float | None] = {}
    for r in prev_rows:
        code = _code_of(r)
        if code in names_by_code and code not in prev_close_by_code:
            prev_close_by_code[code] = _num(_get(r, *_CLOSE))
    basic_by_code: dict[str, dict[str, Any]] = {}
    for r in basic_rows:
        code = _code_of(r)
        if code in names_by_code and code not in basic_by_code:
            basic_by_code[code] = r

    rows: list[dict[str, Any]] = []
    missing = 0
    for code, name in names_by_code.items():
        day_row = daily_by_code.get(code)
        if day_row is None:
            missing += 1
            continue
        close = _num(_get(day_row, *_CLOSE))
        pct = _num(_get(day_row, *_PCT))
        prev_close = prev_close_by_code.get(code)
        week_pct = None
        if close is not None and prev_close not in (None, 0):
            week_pct = round((close - prev_close) / prev_close * 100, 4)
        basic = basic_by_code.get(code)
        turnover = _num(_get(basic, "turnover_rate")) if basic is not None else None
        total_mv = _num(_get(basic, "total_mv")) if basic is not None else None
        market_cap = round(total_mv / 1e4, 2) if total_mv is not None else None
        rows.append(
            {
                "name": name,
                "ts_code": code,
                "pct": pct,
                "pct_text": _fmt_pct(pct) if pct is not None else None,
                "close": close,
                "close_text": _fmt_price(close) if close is not None else None,
                "week_pct": week_pct,
                "week_pct_text": _fmt_pct(week_pct) if week_pct is not None else None,
                "turnover": turnover,
                "market_cap": market_cap,
                "remark": "",
            }
        )

    # 排序：跌幅/涨幅不重要 —— 保持池序（watchlist.json 顺序），display_limit 截断
    total = len(rows)
    truncated = total > display_limit
    return {
        "rows": rows[:display_limit],
        "pool_count": len(names_by_code),
        "row_count": total,
        "truncated": truncated,
        "total_count": total,
        "missing_codes": missing,
        "stats": pool_stats(rows),
        "_all_rows": rows,
    }


def pool_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """全池涨跌统计（**必须传未截断的行**）：涨/跌/平家数、均值、中位数、参与统计的只数。

    只统计 `pct` 为数值的行；`computed_over` 让前端能诚实标注口径
    （旧报文没有这个字段时前端会退回自算，那就只有 display_limit 那部分，必须标「样本」）。
    """
    pcts = [r["pct"] for r in rows if isinstance(r.get("pct"), (int, float))]
    if not pcts:
        return {"up": 0, "down": 0, "flat": 0, "avg_pct": None, "median_pct": None, "computed_over": 0}
    ordered = sorted(pcts)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {
        "up": sum(1 for x in pcts if x > 0),
        "down": sum(1 for x in pcts if x < 0),
        "flat": sum(1 for x in pcts if x == 0),
        "avg_pct": round(sum(pcts) / len(pcts), 4),
        "median_pct": round(median, 4),
        "computed_over": len(pcts),
    }


def project_announce(
    rows: list[dict[str, Any]], names_by_code: dict[str, str], limit: int = 20
) -> dict[str, Any]:
    """公告行投影：ts_code/name/type/title/ann_date；type 优先原生字段、缺失按标题关键词分类。

    去重键 (ts_code, title[:40])；按公告日期降序；limit 截断。
    """
    items: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        code = _code_of(r)
        title = _text(_get(r, *_ANN_CONTENT))
        if not code and not title:
            continue
        name = names_by_code.get(code, _name_of(r))
        ann_type = _text(_get(r, *_ANN_TYPE))
        if not ann_type:
            ann_type = _classify_ann_title(title)
        key = (code or title, title[:40])
        if key in items:
            continue
        items[key] = {
            "ts_code": code,
            "name": name,
            "type": ann_type,
            "title": title,
            "ann_date": str(_get(r, *_ANN_DATE) or ""),
        }
    out = sorted(items.values(), key=lambda x: (x["ann_date"] or "", x["title"]), reverse=True)
    return {"items": out[:limit], "limit": limit}


def _classify_ann_title(title: str) -> str:
    for keywords, label in _ANN_TYPE_RULES:
        if any(k in title for k in keywords):
            return label
    return "其他"


def project_forecast(
    rows: list[dict[str, Any]], names_by_code: dict[str, str], *, up: float, down: float
) -> dict[str, Any]:
    """业绩预告异动：只保留触发阈值的行；yoy 取值走 predicate（含 p_change_min/max、yoy_net_profit 别名）。

    hits ∈ ["up"]/["down"]/["up","down"]；yoy_text 区间 "60.00% ~ 110.00%" / 单值 "102.93%"。
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        code = _code_of(r)
        p_min, p_max = yoy_of(r)
        hit, hits = range_hit(p_min, p_max, up=up, down=down)
        if not hit:
            continue
        reason = _text(_get(r, *_FOR_REASON))
        items.append(
            {
                "ts_code": code,
                "name": names_by_code.get(code, _name_of(r)),
                "scope": scope_of(r),
                "yoy_min": p_min,
                "yoy_max": p_max,
                "yoy_text": _fmt_range(p_min, p_max),
                "hits": hits,
                "reason": reason,
            }
        )
    return {"items": items, "hit_count": len(items)}


def _fmt_range(p_min: float | None, p_max: float | None) -> str:
    """区间/单值同比文本：'60.00% ~ 110.00%'（单值 p_min==p_max → '102.93%'）。"""
    if p_min is not None and p_max is not None and p_min != p_max:
        return f"{p_min:.2f}% ~ {p_max:.2f}%"
    v = p_min if p_min is not None else p_max
    return f"{v:.2f}%" if v is not None else "—"


def project_news(rows: list[dict[str, Any]], limit: int = 20) -> dict[str, Any]:
    """新闻/催化行投影：title/src/datetime/url。"""
    items: list[dict[str, Any]] = []
    for r in rows:
        title = _text(_get(r, *_NEWS))
        if not title:
            continue
        src, dt = news_meta_of(r)
        items.append({"title": title, "src": src, "datetime": dt, "url": news_url_of(r)})
    return {"items": items[:limit]}


# ---------------------------------------------------------------------------
# 一句话研判（确定性规则；自持，与 tracker_render._brief 语义一致）
# ---------------------------------------------------------------------------

def build_brief(
    board_rows: list[dict[str, Any]],
    inflow_top: list[dict[str, Any]],
    forecast_items: list[dict[str, Any]],
    watch_rows: list[dict[str, Any]] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """规则拼接（目标 70~150 字）：板块涨跌 → 资金流入 TOP1/3 → 标的池涨跌统计 → 涨幅居前 TOP2 →
    业绩预告触发 → 公告/催化计数；完全无素材 → 「数据不足，无法研判。」；[:150] 截断。"""
    parts: list[str] = []
    # 1) 板块/指数当日涨跌（首行）
    if board_rows and board_rows[0].get("pct_text"):
        parts.append(f"{board_rows[0]['name']}（{board_rows[0]['pct_text']}）")
    # 2) 资金净流入 TOP1（TOP2/3 有名则并入，避免只提一只）
    if inflow_top:
        tops = [f"{x['name']}（{x['inflow_text']}亿）" for x in inflow_top[:3] if x.get("inflow_text")]
        if tops:
            parts.append(f"资金净流入居前：{'、'.join(tops)}")
    # 3) 标的池涨跌统计（有 pct 的行才统计）
    if watch_rows:
        pcts = [r["pct"] for r in watch_rows if isinstance(r.get("pct"), (int, float))]
        if pcts:
            up = sum(1 for p in pcts if p > 0)
            down = sum(1 for p in pcts if p < 0)
            avg = sum(pcts) / len(pcts)
            parts.append(f"标的池 {up} 涨 {down} 跌、均幅 {avg:+.2f}%")
        # 4) 涨幅居前 TOP2（按数值 pct 降序——字符串排序会被 "+10%" < "+5%" 坑）
        gainers = [
            (r["name"], r.get("pct_text"))
            for r in sorted(
                (r for r in watch_rows if isinstance(r.get("pct"), (int, float)) and r["pct"] > 0 and r.get("pct_text")),
                key=lambda r: r["pct"],
                reverse=True,
            )[:2]
        ]
        if gainers:
            parts.append("涨幅居前：" + "、".join(f"{n}（{t}）" for n, t in gainers))
    # 5) 业绩预告触发（带 yoy 区间）
    if forecast_items:
        first = forecast_items[0]
        parts.append(
            f"{first['name']} 业绩预告{'（' + first['yoy_text'] + '）' if first.get('yoy_text') and first['yoy_text'] != '—' else ''}触发异动"
        )
    # 6) 公告/催化计数
    if counts:
        if counts.get("announce"):
            parts.append(f"公告 {counts['announce']} 条")
        if counts.get("news"):
            parts.append(f"催化 {counts['news']} 条")
    if not parts:
        return {"text": "数据不足，无法研判。", "chars": 9}
    # 150 字上限：优先保高价值素材（板块/资金流/池统计/涨幅居前/异动），
    # 超限时先回退丢弃低价值的「计数」素材（公告/催化 N 条）再重拼，仍超才截断（截断会丢句尾信息）。
    text = "；".join(parts) + "。"
    if len(text) > 150:
        low_value = [p for p in parts if p.startswith(("公告 ", "催化 "))]
        if low_value:
            text = "；".join(p for p in parts if p not in low_value) + "。"
    return {"text": text[:150], "chars": min(len(text), 150)}
