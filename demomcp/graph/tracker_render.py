"""AI算力产业链高频跟踪快报 —— 确定性模板渲染器（不叫 LLM 写文案）。

把 tool 证据（每条 `{source_type, source, content, params?}`）按接口名/字段归类到模板六段，用容错 JSON 解析 +
字段别名匹配，渲染成固定 Markdown 结构：板块概览 / 标的池行情速览 / 关键公告 / 业绩预告异动提示 / 产业链催化事件 /
一句话研判（规则拼接，≤150 字）。某段无数据 → 明确标『数据未接入』/『本期无…』；绝不编造。

设计取向：尽力匹配 + 缺则未接入——不为每个接口写死解析器；字段别名表覆盖常见字段，匹配不到就不填/标未接入。
注意：证据 content 在 tool_rag 被截断为 `_MAX_EVIDENCE_CHARS`，若截断恰在 JSON 中途，解析失败 → 该段未接入（安全退化，不造假）。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

_NA = "数据未接入"  # 板块/标的池等整段缺失标识


# ---------------------------------------------------------------------------
# 容错解析
# ---------------------------------------------------------------------------
def _extract_rows(content: str) -> list[dict[str, Any]]:
    """把工具返回文本解析成行 dict 列表。

    兼容：裸数组、`{data:[...]}`、`{ok,data}`、代理 `{code,msg,row_count,data}`、裸 dict（单行）。
    业务信封（有 code/msg/ok/row_count 且无 data）→ 空；解析失败 → 空。
    """
    try:
        body = json.loads(content)
    except (ValueError, TypeError):
        return []
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            return [data]
        if any(k in body for k in ("code", "msg", "ok", "row_count", "rowcount")):
            return []
        return [body]
    return []


def _get(row: dict[str, Any], *aliases: str) -> Any:
    """按别名优先级取 row 里第一个命中字段的值（别名小写子串匹配）。"""
    for a in aliases:
        low = a.lower()
        for k, v in row.items():
            if low in k.lower():
                return None if v is None or v == "" else v
    return None


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _fmt_pct(v: Any) -> str:
    n = _num(v)
    if n is None:
        return "—"
    return f"{n:+.2f}%"


def _fmt_price(v: Any) -> str:
    n = _num(v)
    if n is None:
        return str(v or "—")
    return f"{n:.2f}元"


def _yi_num(v: Any) -> float | None:
    """净流入金额 → 亿元数值：|值|≥1000 视为万元（/1e4），否则视为已有亿元；近似口径。"""
    n = _num(v)
    if n is None:
        return None
    return n / 1e4 if abs(n) >= 1000 else n


def _to_yi(v: Any) -> str:
    """净流入金额转「亿元」展示字符串。"""
    yi = _yi_num(v)
    return "—" if yi is None else f"{yi:.2f}"


def _text(v: Any) -> str:
    return str(v).strip() if v is not None else ""


# ---------------------------------------------------------------------------
# 字段别名（按优先级顺序）
# ---------------------------------------------------------------------------
_NAME = ("证券简称", "股票名称", "标的名称", "name", "shortname", "short_name", "ts_name", "symbol", "ts_code")
_PCT = ("pct_change", "pctchg", "change_pct", "涨跌幅", "change_rate", "pct_chg")
_CLOSE = ("close", "latest_price", "最新价", "收盘价", "trade_price", "last_close", "price")
_INFLOW = ("main_net_inflow", "net_main_amount", "net_amount", "net_inflow", "main_amount", "主力净流入", "net_mf_amount")
_WEEK = ("week_pct", "weekly_pct", "周涨跌幅", "本周涨幅", "week_chg")
_REMARK = ("备注", "remark", "note", "desc", "summary", "reason")
_ANN_TYPE = ("类型", "ann_type", "event_type", "title_type", "公告类型", "type")
_ANN_CONTENT = ("核心内容", "content", "title", "ann_content", "公告内容", "summary")
_ANN_DATE = ("发布日期", "ann_date", "publish_date", "notice_date", "date", "公告日期")
_FOR_YOY = ("net_profit_yoy", "net_profit_yoy_change", "净利润同比", "profit_yoy", "同比", "yoy", "change_pct", "pct_change")
_FOR_REASON = ("归因", "reason", "explanation", "change_reason", "summary", "content")
_NEWS = ("title", "新闻标题", "content", "summary", "text", "新闻内容")


def _name_of(row: dict[str, Any]) -> str:
    return _text(_get(row, *_NAME))


# ---------------------------------------------------------------------------
# 证据分类（按接口名/关键词）
# ---------------------------------------------------------------------------
def _classify(source: str) -> str:
    s = (source or "").lower()
    if re.search(r"forecast|express|预告|expect|profit_yoy", s):
        return "forecast"
    if re.search(r"announce|anns|notice|公告", s):
        return "announce"
    if re.search(r"news|新闻|stock_news|xw|industry_news", s):
        return "news"
    if re.search(r"board|concept|sector|industry|板块|资金流|moneyflow|ths|index|flow", s):
        return "board"
    if re.search(r"daily|basic|quote|行情|price|realtime|kline", s):
        return "watchlist"
    return "other"


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        lines.append("| " + " | ".join("—" if c is None or c == "" else str(c) for c in r) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 一句话研判（确定性规则；无 LLM）
# ---------------------------------------------------------------------------
def _brief(board_pct: list[tuple[str, str]], inflow_top: list[tuple[str, str]], forecast_rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if inflow_top:
        parts.append(f"资金净流入居前：{inflow_top[0][0]}（{inflow_top[0][1]}亿）")
    if board_pct:
        parts.append(f"板块当日涨跌：{board_pct[0][0]}（{board_pct[0][1]}）")
    if forecast_rows:
        parts.append(f"{_name_of(forecast_rows[0])} 业绩预告触发异动")
    if not parts:
        return "数据不足，无法研判。"
    brief = "；".join(parts)
    return (brief + "。")[:150]


# ---------------------------------------------------------------------------
# 主渲染
# ---------------------------------------------------------------------------
def render_tracker(evidence: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    """把 evidence 渲染成六段 Markdown 快报。ctx: {query, date, request}。"""
    q = _text(ctx.get("query")) or "该板块"
    day = _text(ctx.get("date")) or datetime.now(UTC).date().isoformat()
    section: dict[str, list[dict[str, Any]]] = {"board": [], "watchlist": [], "announce": [], "forecast": [], "news": []}
    for e in evidence:
        kind = _classify(_text(e.get("source")))
        if kind in section:
            rows = _extract_rows(_text(e.get("content")))
            section[kind].extend(rows)

    out: list[str] = [f"### 【{_board_name(q)}跟踪快报·{day}】", ""]

    # 一、板块概览
    out.extend(_section_board(section["board"]))
    # 二、标的池行情速览
    out.extend(_section_watchlist(section["watchlist"]))
    # 三、关键公告
    out.extend(_section_announce(section["announce"]))
    # 四、业绩预告异动提示
    out.extend(_section_forecast(section["forecast"]))
    # 五、产业链催化事件
    out.extend(_section_news(section["news"]))
    # 六、一句话研判
    board_pct, inflow_top, forecast_hit = _summary_data(section)
    out.extend(["\n#### 六、一句话研判", "", f"> {_brief(board_pct, inflow_top, forecast_hit)}"])

    return "\n".join(out)


def _board_name(q: str) -> str:
    """从用户问题里尽量抽产业链/板块名（只去「生成/今日/跟踪快报/的」等泛词，保留「产业链/板块」）。"""
    s = q
    for tok in ("跟踪快报", "生成", "今日", "每天", "每日", "每日观察", "的", "看看", "请"):
        s = s.replace(tok, "")
    s = re.sub(r"\s+", "", s)
    return s[:20] or "该板块"


def _section_board(rows: list[dict[str, Any]]) -> list[str]:
    out = ["\n#### 一、板块概览", ""]
    if not rows:
        out.extend([f"**{_NA}**", ""])
    else:
        # 主行：带涨跌幅的板块/概念
        main = [r for r in rows if _get(r, *_PCT) is not None] or rows[:1]
        tbl = _md_table(
            ["概念/板块", "当日涨跌幅", "主力资金净流入(亿元)", "备注"],
            [[_name_of(r), _fmt_pct(_get(r, *_PCT)), _to_yi(_get(r, *_INFLOW)) if _get(r, *_INFLOW) is not None else "—", _text(_get(r, *_REMARK))] for r in main],
        )
        out.append(tbl if tbl else f"**{_NA}**")
    # TOP5 资金净流入
    inflow = [
        (_text(_get(r, *_NAME)), _yi_num(_get(r, *_INFLOW)))
        for r in rows if _get(r, *_INFLOW) is not None and _text(_get(r, *_NAME))
    ]
    inflow.sort(key=lambda x: x[1] or 0.0, reverse=True)
    inflow = inflow[:5]
    out.extend(["", "**本周资金净流入 TOP5**", ""])
    if inflow:
        out.append(_md_table(["排名", "标的", "净流入(亿元)"], [[i + 1, n, f"{v:.2f}"] for i, (n, v) in enumerate(inflow)]))
    else:
        out.append(f"**{_NA}**")
    return out


def _section_watchlist(rows: list[dict[str, Any]]) -> list[str]:
    out = ["\n#### 二、标的池行情速览", ""]
    if not rows:
        out.append(f"**{_NA}（标的池未接入）**")
    else:
        tbl = _md_table(
            ["标的", "当日涨跌", "最新价", "本周涨幅", "备注"],
            [[_name_of(r), _fmt_pct(_get(r, *_PCT)), _fmt_price(_get(r, *_CLOSE)), _fmt_pct(_get(r, *_WEEK)), _text(_get(r, *_REMARK))] for r in rows],
        )
        out.append(tbl)
    return out


def _section_announce(rows: list[dict[str, Any]]) -> list[str]:
    out = ["\n#### 三、关键公告", ""]
    if not rows:
        out.append("**本期无关键公告**")
    else:
        tbl = _md_table(
            ["标的", "公告类型", "核心内容", "发布日期"],
            [[_name_of(r), _text(_get(r, *_ANN_TYPE)), _text(_get(r, *_ANN_CONTENT)), _text(_get(r, *_ANN_DATE))] for r in rows],
        )
        out.append(tbl)
    return out


def _section_forecast(rows: list[dict[str, Any]]) -> list[str]:
    out = ["\n#### 四、业绩预告异动提示", ""]
    # 仅净利同比 >50% 或 <-20% 才列出
    hits = [r for r in rows if (_num(_get(r, *_FOR_YOY)) or 0.0) > 50 or (_num(_get(r, *_FOR_YOY)) or 0.0) < -20]
    if not hits:
        out.append("**本期无异常**")
    else:
        tbl = _md_table(
            ["标的", "预告净利润同比", "归因"],
            [[_name_of(r), _fmt_pct(_get(r, *_FOR_YOY)), _text(_get(r, *_FOR_REASON))] for r in hits],
        )
        out.append(tbl)
    return out


def _section_news(rows: list[dict[str, Any]]) -> list[str]:
    out = ["\n#### 五、产业链催化事件", ""]
    if not rows:
        out.append("**本期无明显催化**")
    else:
        bullets = [_text(_get(r, *_NEWS)) or json.dumps(r, ensure_ascii=False)[:120] for r in rows]
        out.extend(f"- {b}" for b in bullets if b)
    return out


def _summary_data(section: dict[str, list[dict[str, Any]]]) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[dict[str, Any]]]:
    """整理一句话研判需要的素材：板块涨跌、TOP 资金流、业绩预告异动行。"""
    board = section["board"]
    board_pct = [(_text(_get(r, *_NAME)), _fmt_pct(_get(r, *_PCT))) for r in board if _get(r, *_PCT) is not None and _text(_get(r, *_NAME))]
    inflow = [
        (_text(_get(r, *_NAME)), _yi_num(_get(r, *_INFLOW)))
        for r in board if _get(r, *_INFLOW) is not None and _text(_get(r, *_NAME))
    ]
    inflow.sort(key=lambda x: x[1] or 0.0, reverse=True)
    inflow_top = [(n, f"{v:.2f}") for n, v in inflow[:1]]
    forecast_hit = [r for r in section["forecast"] if (_num(_get(r, *_FOR_YOY)) or 0.0) > 50 or (_num(_get(r, *_FOR_YOY)) or 0.0) < -20]
    return board_pct, inflow_top, forecast_hit
