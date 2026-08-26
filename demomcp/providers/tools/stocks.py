"""客户端语义工具层：限定「比亚迪 / 宁德时代」两家上市公司，包裹底层 MCP 取数。

`StockToolProvider` 实现 `ToolProvider` 协议（`list_tools` / `call_tool`，结构化，无需继承）。
- `list_tools()` 只暴露精选语义工具，隐藏底层通用 `query/list_apis/get_api_info` —— 实现「只查两家」的硬约束。
- `call_tool()` 把语义调用翻译成底层接口调用（经 `_fetch_interface` 委托给被注入的底层 `ToolProvider`），
  并在其上做代码解析（硬 allowlist）、日期/复权/期次归一化与业务错误处理。

底层返回契约假定为 `{code, msg, row_count, data}`（本地 `mcp_server` 代理形状）；若底层只暴露
`query(api_name, params, fields)`，`_fetch_interface` 会在「未知接口工具」时回退到 `query`。
校验/业务类失败返回 `is_error=False` 的友好文本（LLM 可自纠）；仅真正的取数异常返回 `is_error=True`。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.mcp import _parse_result, _permission_signal, _short_msg

# ---- 常量：两公司硬 allowlist ----
ALLOWLIST: tuple[str, ...] = ("002594.SZ", "300750.SZ")
STOCK_NAMES: dict[str, str] = {
    "002594.SZ": "比亚迪",
    "300750.SZ": "宁德时代",
}

# 别名 → ts_code（大小写不敏感、去空白）
_ALIASES: dict[str, str] = {
    "002594": "002594.SZ",
    "002594.sz": "002594.SZ",
    "比亚迪": "002594.SZ",
    "比亚迪股份": "002594.SZ",
    "byd": "002594.SZ",
    "300750": "300750.SZ",
    "300750.sz": "300750.SZ",
    "宁德时代": "300750.SZ",
    "宁德": "300750.SZ",
    "catl": "300750.SZ",
}

_PERIOD_LABEL = {"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "年报"}
_QUARTER_ENDS = ("0331", "0630", "0930", "1231")


# ---- 异常：统一映射到 is_error 语义 ----
class StockInputError(ValueError):
    """参数校验类错误（硬 allowlist / 非法日期 / 复权 / 期次）→ 友好、is_error=False，让 LLM 自纠。"""


class StockBusinessError(RuntimeError):
    """业务失败（code!=0 / 空数据 / 底层友好提示）→ is_error=False，如实转述，不崩图。"""

    def __init__(self, msg: str, *, permission: bool = False) -> None:
        super().__init__(msg)
        self.permission = permission


class StockFetchError(RuntimeError):
    """真正的取数异常（底层 is_error / 传输失败）→ is_error=True。"""


# ---- 语义工具定义 ----
_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="stock_available",
        description="列出可使用语义工具查询的上市公司（比亚迪 002594.SZ / 宁德时代 300750.SZ）。",
        input_schema={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="stock_realtime_quote",
        description=(
            "获取某公司实时/最新报价：最新价、当日涨跌幅、市盈率(pe_ttm)、市净率(pb)、总市值；"
            "实时数据不可用时回落最近交易日 EOD。"
        ),
        input_schema={
            "type": "object",
            "properties": {"stock": {"type": "string", "description": "公司名/代码/别名，如 比亚迪、002594、BYD"}},
            "required": ["stock"],
        },
    ),
    ToolSpec(
        name="stock_price_range",
        description="获取某公司区间行情与区间涨跌幅（默认前复权 qfq，可切后复权 hfq / 不复权 none）。",
        input_schema={
            "type": "object",
            "properties": {
                "stock": {"type": "string", "description": "公司名/代码/别名"},
                "start": {"type": "string", "description": "起始日期（YYYY-MM-DD / YYYYMMDD / 相对词如近一年），默认近一年"},
                "end": {"type": "string", "description": "结束日期，默认最近交易日/今日"},
                "adj": {"type": "string", "enum": ["qfq", "hfq", "none"], "description": "复权口径，默认 qfq"},
            },
            "required": ["stock"],
        },
    ),
    ToolSpec(
        name="stock_financials",
        description="获取某公司近 1~2 年核心财务指标：营收、归母净利润、毛利率、资产负债率（各期）。",
        input_schema={
            "type": "object",
            "properties": {
                "stock": {"type": "string", "description": "公司名/代码/别名"},
                "period": {
                    "type": "string",
                    "description": "近一年 / 近两年(默认) / 2024年报 / 2024Q1 / 20240331",
                },
            },
            "required": ["stock"],
        },
    ),
]


# ---- 纯函数校验层（可测） ----
def _norm(token: Any) -> str:
    """归一化匹配串：小写、去首尾空白、去内部空格。"""
    return str(token).strip().lower().replace(" ", "")


def _today() -> date:
    """当前日期（tz 感知，规避 DTZ011）。常取 UTC 日；关键路径可显式传 today 参数。"""
    return datetime.now(UTC).date()


def resolve_stock(token: Any, *, allowlist: tuple[str, ...] = ALLOWLIST) -> str:
    """把公司名/别名/代码/部分 → ts_code；不在 allowlist 一律友好拒绝。"""
    if token is None or not str(token).strip():
        raise StockInputError("缺少标的参数：请指定比亚迪(002594.SZ)或宁德时代(300750.SZ)。")
    ts_code = _ALIASES.get(_norm(token))
    if ts_code is None or ts_code not in allowlist:
        raise StockInputError(
            f"[仅支持两家公司：比亚迪(002594.SZ)、宁德时代(300750.SZ)。未支持：{str(token).strip()}]"
        )
    return ts_code


def parse_date(token: Any, *, today: date | None = None) -> str:
    """把多种日期格式 / 相对词归一成 YYYYMMDD。"""
    if today is None:
        today = _today()
    if isinstance(token, date):  # datetime 是 date 子类
        return token.strftime("%Y%m%d")
    if isinstance(token, (int, float)):
        token = str(int(token))
    s = str(token).strip()
    if not s:
        raise StockInputError("缺少日期参数。")
    rel = _RELATIVE_DATE.get(_norm(s))
    if rel is not None:
        return rel(today).strftime("%Y%m%d")
    digits = re.sub(r"\D", "", s)
    if len(digits) != 8:
        m = re.match(r"^(\d{4})[-\/\.](\d{1,2})[-\/\.](\d{1,2})$", s)
        if m:
            digits = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        else:
            raise StockInputError(
                f"无法解析日期：{s}（支持 20260801 / 2026-08-01 / 相对词如 近一年、今天）"
            )
    _check_date(digits, s)
    return digits


def _check_date(digits: str, raw: Any) -> None:
    try:
        date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError as exc:
        raise StockInputError(f"非法日期：{raw}（{digits}）") from exc


def _rel_days(days: int) -> Any:
    return lambda t: t - timedelta(days=days)


_RELATIVE_DATE = {
    "今天": lambda t: t,
    "today": lambda t: t,
    "近一月": _rel_days(30),
    "近一个月": _rel_days(30),
    "近1月": _rel_days(30),
    "近一季度": _rel_days(90),
    "近一个季度": _rel_days(90),
    "近1季": _rel_days(90),
    "近一年": _rel_days(365),
    "最近一年": _rel_days(365),
    "近1年": _rel_days(365),
    "近两年": _rel_days(730),
    "近2年": _rel_days(730),
    "今年": lambda t: date(t.year, 1, 1),
    "去年": lambda t: date(t.year - 1, 12, 31),
    "本季": lambda t: date(t.year, _quarter_start_month(t.month), 1),
    "上季": lambda t: _prev_quarter_start(t),
}


def _quarter_start_month(month: int) -> int:
    return (((month - 1) // 3) * 3) + 1


def _prev_quarter_start(t: date) -> date:
    cur = (_quarter_start_month(t.month), t.year)
    if cur == (1, t.year):
        return date(t.year - 1, 10, 1)
    return date(t.year, _quarter_start_month(t.month) - 3, 1)


def normalize_date_range(
    start: Any = None,
    end: Any = None,
    *,
    today: date | None = None,
    default_days: int = 365,
) -> tuple[str, str]:
    """归一化一个日期区间，返回 (YYYYMMDD, YYYYMMDD)。未来结束日期按今日截断（容错）。"""
    if today is None:
        today = _today()
    if end is None:
        end_date = today
    else:
        end_date = _pd(parse_date(end, today=today))
    if start is None:
        start_date = end_date - timedelta(days=default_days)
    else:
        start_date = _pd(parse_date(start, today=today))
    if start_date > end_date:
        raise StockInputError(f"起始日期晚于结束日期：{start_date} > {end_date}")
    if end_date > today:
        end_date = today
        if start_date > end_date:
            start_date = end_date - timedelta(days=default_days)
    return start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")


def _pd(ymd: str) -> date:
    return date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))


def normalize_adj(token: Any, *, default: str = "qfq") -> str:
    """归一化复权口径 → 'qfq'(默认)/'hfq'/'none'。"""
    if token is None or str(token).strip() == "":
        return default
    v = _norm(token)
    if v in ("qfq", "前复权"):
        return "qfq"
    if v in ("hfq", "后复权"):
        return "hfq"
    if v in ("none", "不复权"):
        return "none"
    raise StockInputError(f"复权口径仅支持 qfq / hfq / none：{token}")


def normalize_period(value: Any = None, *, periods: int = 8, today: date | None = None) -> list[str]:
    """把期次表达归一成升序的报告期列表（YYYYMMDD）。默认近 8 期（2 年）。"""
    if today is None:
        today = _today()
    if value is None or not str(value).strip():
        return _last_quarter_ends(periods, today)
    s = _norm(value)
    if s in ("近一年", "近1年", "最近一年", "一年"):
        return _last_quarter_ends(4, today)
    if s in ("近两年", "近2年", "最近两年", "两年", "近两年"):
        return _last_quarter_ends(periods or 8, today)
    m = re.match(r"^(\d{4})(年报|年度)?$", s)
    if m:
        return [f"{m.group(1)}1231"]
    m = re.match(r"^(\d{4})[qQ]([1-4])$", s) or re.match(r"^(\d{4})([1-4])[qQ]$", s)
    if m:
        return [f"{m.group(1)}{_QUARTER_ENDS[int(m.group(2)) - 1]}"]
    if re.fullmatch(r"\d{8}", s):
        return [s]
    raise StockInputError(f"无法解析期次：{value}（支持 近一年 / 近两年 / 2024年报 / 2024Q1 / 20240331）")


def _last_quarter_ends(n: int, today: date) -> list[str]:
    n = max(n, 1)
    pool: list[date] = []
    for year in range(today.year - (n // 4 + 2), today.year + 1):
        for md in _QUARTER_ENDS:
            d = date(year, int(md[:2]), int(md[2:]))
            if d <= today:
                pool.append(d)
    pool.sort()
    return [d.strftime("%Y%m%d") for d in pool[-n:]]


def period_label(period: str) -> str:
    """20240331 → 2024Q1；20241231 → 2024年报。"""
    year, md = period[:4], period[4:]
    return f"{year}{_PERIOD_LABEL.get(md, '期')}"


def _safe_num(v: Any) -> float | int | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _parse_rows(content: str) -> list[dict[str, Any]]:
    """把底层返回文本解析成行列表：官方 MCP 返回 JSON 数组；本地代理返回 {code,msg,row_count,data}。"""
    body = _parse_result(content)
    if body is None:
        try:
            arr = json.loads(content)
        except (ValueError, TypeError):
            arr = None
        if isinstance(arr, list):
            return arr
        raise StockBusinessError(_short_msg(content))
    code = body.get("code", 0)
    if isinstance(code, (int, float)) and code != 0:
        permission, _ = _permission_signal(content)
        raise StockBusinessError(str(body.get("msg") or "业务失败"), permission=permission)
    data = body.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data and "fields" in data:  # 本地代理 {data:{fields,items}}
        return [dict(zip(data["fields"], row)) for row in data["items"]]
    if isinstance(data, dict):
        return [data]
    return data or []


# ---- 语义工具层 ----
class StockToolProvider:
    """实现 ToolProvider：只暴露语义工具，把语义调用翻译成底层接口调用。"""

    def __init__(self, underlying: Any, *, allowlist: tuple[str, ...] = ALLOWLIST, config: Any = None) -> None:
        self._underlying = underlying  # 底层 ToolProvider（通常是 MCPToolProvider；测试可注入假实现）
        self._allowlist = allowlist
        self._default_adj = getattr(config, "stock_default_adj", "qfq") if config else "qfq"
        self._fin_periods = int(getattr(config, "stock_financial_periods", 8) or 8) if config else 8
        self._tool_specs: list[ToolSpec] | None = None

    async def list_tools(self) -> list[ToolSpec]:
        if self._tool_specs is None:
            self._tool_specs = list(_TOOL_SPECS)
        return list(self._tool_specs)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = dict(arguments or {})
        try:
            if name == "stock_available":
                return self._cmd_available()
            if name == "stock_realtime_quote":
                return await self._cmd_realtime(args)
            if name == "stock_price_range":
                return await self._cmd_range(args)
            if name == "stock_financials":
                return await self._cmd_financials(args)
            return ToolResult(content=f"Unknown tool: {name}", is_error=True)
        except StockInputError as exc:
            return ToolResult(content=str(exc), is_error=False)
        except StockBusinessError as exc:
            return ToolResult(content=str(exc), is_error=False)
        except StockFetchError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001 - 语义层兜底，循环永不崩
            return ToolResult(content=f"Error: {type(exc).__name__}: {exc}", is_error=True)

    # ---- 语义命令 ----
    def _cmd_available(self) -> ToolResult:
        companies = [{"ts_code": ts, "name": name, "allowlist": ts in self._allowlist} for ts, name in STOCK_NAMES.items() if ts in self._allowlist]
        return _render_ok("stock_available", {"companies": companies}, source={"api": []})

    async def _cmd_realtime(self, args: dict[str, Any]) -> ToolResult:
        ts = resolve_stock(args.get("stock"), allowlist=self._allowlist)
        company = STOCK_NAMES.get(ts, ts)
        rows: list[dict[str, Any]] = []
        try:
            rows = await self._fetch_interface("realtime_quote", {"ts_code": ts})
        except (StockFetchError, StockBusinessError):
            rows = []  # 实时不可用/无权限 → 回落 EOD
        if rows:
            r = rows[0]
            data = {
                "ts_code": ts,
                "name": company,
                "trade_date": str(r.get("trade_date") or r.get("date") or ""),
                "price": _safe_num(r.get("price") or r.get("last") or r.get("close")),
                "pct_change": _safe_num(r.get("pct_change")),
                "pe": _safe_num(r.get("pe") or r.get("pe_ttm")),
                "pe_ttm": _safe_num(r.get("pe_ttm") or r.get("pe")),
                "pb": _safe_num(r.get("pb")),
                "total_mv": _safe_num(r.get("total_mv") or r.get("market_cap")),
                "volume": _safe_num(r.get("volume") or r.get("vol")),
                "amount": _safe_num(r.get("amount")),
                "source": "realtime_quote",
            }
            return _render_ok("stock_realtime_quote", data, company, ts, {"api": ["realtime_quote"]})

        # 回落：最近交易日 EOD（daily + daily_basic）
        today = _today()
        start = (today - timedelta(days=10)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        drows = await self._fetch_interface("daily", {"ts_code": ts, "start_date": start, "end_date": end}, fields="ts_code,trade_date,close,pct_chg,vol,amount")
        brows = await self._fetch_interface("daily_basic", {"ts_code": ts, "start_date": start, "end_date": end}, fields="ts_code,trade_date,pe_ttm,pb,total_mv") or []
        if not drows:
            raise StockBusinessError(f"{company} 无最新行情数据（实时接口未返回且无最近交易日数据）。")
        last_d = max(drows, key=lambda r: str(r.get("trade_date", "")))
        last_b = max(brows, key=lambda r: str(r.get("trade_date", ""))) if brows else {}
        data = {
            "ts_code": ts,
            "name": company,
            "trade_date": str(last_d.get("trade_date", "")),
            "price": _safe_num(last_d.get("close")),
            "pct_change": _safe_num(last_d.get("pct_chg") or last_d.get("pct_change")),
            "pe_ttm": _safe_num(last_b.get("pe_ttm")),
            "pb": _safe_num(last_b.get("pb")),
            "total_mv": _safe_num(last_b.get("total_mv")),
            "volume": _safe_num(last_d.get("vol")),
            "amount": _safe_num(last_d.get("amount")),
            "source": "daily+daily_basic(最新EOD回落)",
        }
        return _render_ok("stock_realtime_quote", data, company, ts, {"api": ["daily", "daily_basic"], "fallback": True})

    async def _cmd_range(self, args: dict[str, Any]) -> ToolResult:
        ts = resolve_stock(args.get("stock"), allowlist=self._allowlist)
        company = STOCK_NAMES.get(ts, ts)
        adj = normalize_adj(args.get("adj"), default=self._default_adj)
        start, end = normalize_date_range(args.get("start"), args.get("end"))
        try:
            af = await self._fetch_interface("adj_factor", {"ts_code": ts, "start_date": start, "end_date": end}, fields="ts_code,trade_date,adj_factor")
        except (StockBusinessError, StockFetchError):
            af = []  # 复权因子不可用/无权限 → 按不复权（effective_adj=none）
        af = af or []
        daily = await self._fetch_interface("daily", {"ts_code": ts, "start_date": start, "end_date": end}, fields="ts_code,trade_date,open,high,low,close,vol,amount")
        if len(daily) < 2:
            raise StockBusinessError(f"{company} 在 {start}~{end} 区间交易日不足（{len(daily)} 行），无法计算区间涨跌幅。")
        daily.sort(key=lambda r: str(r.get("trade_date", "")))
        factor_map = {str(r.get("trade_date")): (_safe_num(r.get("adj_factor")) or 1.0) for r in af if r.get("trade_date")}
        effective_adj = adj if factor_map else "none"  # 无复权因子则按不复权
        latest_factor = factor_map.get(str(daily[-1].get("trade_date")), 1.0)

        def adj_close(row: dict[str, Any]) -> float:
            c = _safe_num(row.get("close")) or 0.0
            if effective_adj == "none":
                return c
            f = factor_map.get(str(row.get("trade_date")), 1.0)
            return c * f if effective_adj == "hfq" else c * f / latest_factor

        first, last = daily[0], daily[-1]
        base = adj_close(first)
        return_pct = ((adj_close(last) / base) - 1.0) * 100.0 if base else None
        lows = [x for x in (_safe_num(r.get("low")) for r in daily) if x is not None and x < 1e18]
        highs = [x for x in (_safe_num(r.get("high")) for r in daily) if x is not None]
        data: dict[str, Any] = {
            "adj": effective_adj,
            "start": str(first.get("trade_date")),
            "end": str(last.get("trade_date")),
            "sample_days": len(daily),
            "return_pct": round(return_pct, 2) if return_pct is not None else None,
            "first": str(first.get("trade_date")),
            "last": str(last.get("trade_date")),
            "high": max(highs) if highs else None,
            "low": min(lows) if lows else None,
            "last_close": _safe_num(last.get("close")),
            "total_volume": _safe_num(sum((_safe_num(r.get("vol")) or 0.0) for r in daily)),
            "total_amount": _safe_num(sum((_safe_num(r.get("amount")) or 0.0) for r in daily)),
        }
        # 可选：区间首尾 PE/PB
        try:
            basics = await self._fetch_interface("daily_basic", {"ts_code": ts, "start_date": start, "end_date": end}, fields="ts_code,trade_date,pe_ttm,pb") or []
            bmap = {str(r.get("trade_date")): r for r in basics}
            f_td, l_td = str(first.get("trade_date")), str(last.get("trade_date"))
            data["pe_ttm_start"] = _safe_num(bmap.get(f_td, {}).get("pe_ttm"))
            data["pe_ttm_end"] = _safe_num(bmap.get(l_td, {}).get("pe_ttm"))
            data["pb_start"] = _safe_num(bmap.get(f_td, {}).get("pb"))
            data["pb_end"] = _safe_num(bmap.get(l_td, {}).get("pb"))
        except StockFetchError:
            pass
        return _render_ok("stock_price_range", data, company, ts, {"api": ["daily", "adj_factor"], "params": {"ts_code": ts, "start_date": start, "end_date": end, "adj": adj}, "adj": effective_adj})

    async def _cmd_financials(self, args: dict[str, Any]) -> ToolResult:
        ts = resolve_stock(args.get("stock"), allowlist=self._allowlist)
        company = STOCK_NAMES.get(ts, ts)
        periods = normalize_period(args.get("period"), periods=self._fin_periods)
        out: list[dict[str, Any]] = []
        for p in periods:
            try:
                inc = await self._fetch_interface("income", {"ts_code": ts, "period": p}, fields="ts_code,end_date,revenue,total_revenue,n_income,n_income_attr_p")
            except (StockBusinessError, StockFetchError):
                inc = []
            try:
                fin = await self._fetch_interface("fina_indicator", {"ts_code": ts, "period": p}, fields="ts_code,end_date,grossprofit_margin,netprofit_margin,debt_to_assets,roe,eps")
            except (StockBusinessError, StockFetchError):
                fin = []
            ri = inc[0] if inc else {}
            rf = fin[0] if fin else {}
            revenue = _safe_num(ri.get("revenue") if ri.get("revenue") is not None else ri.get("total_revenue"))
            net = _safe_num(ri.get("n_income_attr_p") if ri.get("n_income_attr_p") is not None else ri.get("n_income"))
            out.append({
                "period": p,
                "label": period_label(p),
                "available": bool(ri) or bool(rf),
                "revenue": revenue,
                "net_profit_attr_p": net,
                "gross_margin": _safe_num(rf.get("grossprofit_margin")),
                "debt_ratio": _safe_num(rf.get("debt_to_assets")),
                "net_margin": _safe_num(rf.get("netprofit_margin")),
                "roe": _safe_num(rf.get("roe")),
                "eps": _safe_num(rf.get("eps")),
            })
        if not any(o["available"] for o in out):
            raise StockBusinessError(f"{company} 无可用的指定期次财报数据：{'、'.join(periods)}。")
        return _render_ok("stock_financials", {"periods": out}, company, ts, {"api": ["income", "fina_indicator"], "params": {"ts_code": ts, "periods": periods}})

    # ---- 底层取数 ----
    async def _fetch_interface(self, api_name: str, params: dict[str, Any], fields: str | None = None) -> list[dict[str, Any]]:
        """调用底层 provider 取某接口数据（官方 Tushare MCP：工具名=接口名、扁平入参、fields 数组、返回 JSON 数组）。

        业务/权限失败抛 StockBusinessError（is_error=False）；传输/其它异常抛 StockFetchError（is_error=True）。
        亦兼容本地代理返回的 {code,msg,row_count,data}。
        """
        args: dict[str, Any] = dict(params)
        if fields:
            args["fields"] = [f.strip() for f in fields.split(",") if f.strip()]
        tr = await self._underlying.call_tool(api_name, args)
        if tr.is_error:
            permission, _ = _permission_signal(tr.content)
            if permission:
                raise StockBusinessError(f"接口 {api_name} 调用失败：{_short_msg(tr.content)}", permission=True)
            raise StockFetchError(tr.content)
        return _parse_rows(tr.content)


def _render_ok(tool: str, data: Any, company: str | None = None, ts_code: str | None = None, source: dict[str, Any] | None = None) -> ToolResult:
    """渲染统一输出契约：{ok, tool, company?, ts_code?, data, source}。"""
    payload: dict[str, Any] = {"ok": True, "tool": tool, "data": data, "source": source or {"api": []}}
    if company is not None:
        payload["company"] = company
    if ts_code is not None:
        payload["ts_code"] = ts_code
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")), is_error=False)
