"""取数编排：五段 `asyncio.gather` 并行、段内降级链、`Semaphore` 限流、段级 `wait_for` 超时、errors 收集。

哲学（与 tracker_render / 快报模板一致）：尽力匹配 + 缺则未接入——**不崩溃、不编造**。
每个工具调用包 `try/except Exception`（不捕 `BaseException`，`CancelledError` 照常透传）；
失败 → `errors` 数组 + 该环节降级；段级超时 → 该段 `status="na"` + 「阶段超时」。

数据获取策略（**按真实权限矩阵 2026-09-05 实测落地**，未证实接口运行时容错）：
- ②标的池行情：`daily(trade_date=T-1)` 与 `daily(trade_date=T-6交易日)` 两张全市场快照 + `daily_basic(trade_date=T-1)` 快照，
  按池子过滤后合并（周涨幅 = 两日收盘差）；**不用逐标的逗号串**（官方接口不支持，成本高、易失败）。
- ①板块：核心指数（环3 index_basic(SSE+SZ) → index_daily，实测有权限有数据）；
  ths_index→ths_daily（环1）/ index_classify→sw_daily（环2）多数 token 40203 → 配置清空即跳过；
  板块资金流（moneyflow_ind_* 40203）dc_flow=false 跳过；个股资金流 TOP5 用 `moneyflow(trade_date=…)` 快照按池滤。
- ③公告：**anns_d 全形态 40203 无权限 → 类型化组合源**（全有权限）：
  stk_holdertrade（股东增减持）/ repurchase（回购）/ block_trade（大宗交易）区间/当日全市场 + 业绩类行并入。
- ④预告：`forecast`/`express` **无区间参数**（50101），先 `ann_date=day` 当日全市场快照、空则池内 `ts_code` 逗号批 → 池滤 → 阈值投影。
- ⑤新闻：`news`/`cctv_news` 40203 → Wind `wind_query(api_name="get_financial_news", params={query})` 兜底（M2 后
  具体 Wind 工具**必须**经 wind_query 调用，裸名会 Unknown）→ `_unpack_wind_news` 解 `{data:{items}}` → 均败 na。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

from demomcp.graph.tracker_render import _extract_rows, _get
from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.quickreport.config import ConfigError, WatchlistConfig, cn_tz
from demomcp.quickreport.projection import (
    build_brief,
    join_watchlist,
    project_announce,
    project_board,
    project_forecast,
    project_news,
)
from demomcp.quickreport.source import (
    KIND_FREE,
    KIND_IFIND,
    KIND_TUSHARE,
    source_kind,
)

_PERMISSION_WORDS = ("积分", "权限", "无权限", "提升", "需提高", "需提升", "points")
_BATCH = 30  # 公告/预告逗号批量上限
_FLOW_CANDIDATES = ("moneyflow_ind_dc", "moneyflow_mkt_dc", "moneyflow_cnt_ths", "moneyflow_ind_ths")


class _Ctx:
    """一次生成的共享状态：并发信号量、错误收集、get_api_info 备忘。"""

    def __init__(self, sem: asyncio.Semaphore, stage_timeout: float) -> None:
        self.sem = sem
        self.stage_timeout = stage_timeout
        self.errors: list[dict[str, str]] = []
        self.api_cache: dict[str, dict[str, Any] | None] = {}

    def error(self, stage: str, tool: str, reason: str) -> None:
        self.errors.append({"stage": stage, "tool": tool, "reason": reason})


# ---------------------------------------------------------------------------
# 调用护墙
# ---------------------------------------------------------------------------

async def _call(
    tools: ToolProvider, ctx: _Ctx, tool: str, args: dict[str, Any], *, stage: str
) -> str | None:
    """单工具调用护墙：抛异常 / is_error / 业务失败（code!=0，含权限类）→ None + errors 记录；成功返回 content。

    返回 None 表示该次取数失败（调用方走降级链）；不抛任何东西（除 CancelledError 透传）。
    Tushare 信封 code!=0 时 data 无意义，故业务失败一律按取数失败处理——
    权限/参数/积分等差异只体现在 errors 的 reason 文案里。
    """
    try:
        async with ctx.sem:
            result = await tools.call_tool(tool, args)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 工具层异常一律转未接入，不崩管线
        ctx.error(stage, tool, f"调用异常：{type(exc).__name__}: {exc}")
        return None
    if result.is_error:
        ctx.error(stage, tool, "工具返回错误")
        return None
    reason = _biz_fail_reason(result.content, source_kind(tool))
    if reason is not None:
        ctx.error(stage, tool, reason)
        return None
    return result.content


def _biz_fail_reason(content: str, kind: str = KIND_TUSHARE) -> str | None:
    """信封业务失败 → 失败原因（权限类带「权限受限」前缀）；成功 → None。

    `kind` 缺省 `tushare`，**单参调用的行为与改造前逐字节一致**（`code != 0` 即失败，
    含被 test_biz_fail_reason_classification 锁定的 `{"code": 1}` → 「业务失败：code 非 0」）。
    加这个形参而不是改判据本身，就是因为那条断言：Tushare 语义下 code:1 确实是失败，
    而 iFind 语义下 code:1 恰恰是成功——同一段文本的含义取决于它来自哪个源。

    另识别 **非 JSON 封装失败文本**（实测 2026-09-05）：mcp.py 把权限类失败归一为一句话
    「调用 {name} 失败：该接口需更高积分或当前账号无权限（代理提示：…）」且 is_error=False——
    纯文本解析不成 JSON，若不识别会被当成「返回空」→ 段 status 误标 empty。
    """
    body = _parse_json_dict(content)
    if body is not None:
        # 免费源/代理层的失败形状，**与源无关、必须先判**：`_extract_rows` 的信封探测只认
        # ("code","msg","ok","row_count","rowcount")，`error` 不在其中 → 它会把 {"error": …}
        # 当成**一行数据**返回（tracker_render.py:42-44 实测）。不在这里拦掉，硬失败就会
        # 被误报成 empty（甚至让 `_is_usable` 为真、把错误字典当数据渲染出去）。
        err = body.get("error")
        if err:
            return f"取数失败：{str(err)[:80]}"
        if kind == KIND_IFIND:
            # iFind 成功码是 1（见 mcp_gateway/providers/ifind.py::_SUCCESS_CODE）。
            # 「查不到数据」也仍是 code:1（提示语在 data 里）→ 不算失败，交给解包器判空。
            return _code_fail(body, ok=(None, 1, "1"))
        if kind == KIND_FREE:
            # 免费源没有 code 约定，失败只体现为上面的 error 键。
            return None
        # tushare / wind：wind 没有 code 字段（缺失即成功），tushare 是 code:0 成功。
        return _code_fail(body, ok=(None, 0, "0"))
    return _text_fail_reason(content)


def _code_fail(body: dict[str, Any], *, ok: tuple[Any, ...]) -> str | None:
    """按该源的成功码集合判定信封；失败则给出原因文案。"""
    code = body.get("code")
    if code in ok:
        return None
    msg = str(body.get("msg") or body.get("message") or body.get("subMsg") or "")
    if any(k in msg for k in _PERMISSION_WORDS):
        return f"权限受限：{msg or '积分不足'}"
    return f"业务失败：{msg or 'code 非 0'}"


def _text_fail_reason(content: str) -> str | None:
    """非 JSON 失败特征文本（mcp 层友好提示 / Error calling 兜底 / 「抱歉，您没有接口」）。"""
    if content.startswith("调用") and "失败" in content:
        return "权限受限：" + content[:80]
    if "Error calling" in content or "抱歉，您没有接口" in content:
        return "权限受限：" + content[:80]
    return None


def _parse_json_dict(content: str) -> dict[str, Any] | None:
    try:
        import json

        body = json.loads(content)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def _rows_of(content: str | None, stage: str, tool: str, ctx: _Ctx) -> list[dict[str, Any]]:
    """content → 行 dict 列表；JSON 解析失败/空 → []（截断安全退化）。"""
    if content is None:
        return []
    rows = _extract_rows(content)
    if not rows:
        ctx.error(stage, tool, "返回为空或非表格数据")
    return rows


def _is_usable(rows: list[dict[str, Any]]) -> bool:
    """行非空且去掉全空行后仍有内容。"""
    return any(r for r in rows)


def _parse_ymd(day: str) -> date:
    """YYYYMMDD → date（避免 naive datetime 的 DTZ/时区歧义）。"""
    return date(int(day[:4]), int(day[4:6]), int(day[6:8]))


# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------

async def resolve_report_date(tools: ToolProvider, ctx: _Ctx, report_date: str | None = None) -> str:
    """快报日期（YYYYMMDD）：显式传入即用；否则 trade_cal 解「今天之前的最后一个交易日」（T-1）；
    trade_cal 失败/无权限 → 兜底昨历日（诚实：取不到数则各段自然空）。"""
    if report_date:
        return report_date
    today = datetime.now(cn_tz()).date()
    try:
        args = {
            "exchange": "SSE",
            "start_date": (today - timedelta(days=15)).strftime("%Y%m%d"),
            "end_date": today.strftime("%Y%m%d"),
        }
        content = await _call(tools, ctx, "trade_cal", args, stage="date")
        rows = _rows_of(content, "date", "trade_cal", ctx)
        open_days = [
            str(_get(r, "cal_date"))
            for r in rows
            if str(_get(r, "is_open") or "") in ("1", "1.0")
            and str(_get(r, "cal_date")) < today.strftime("%Y%m%d")
        ]
        if open_days:
            return max(open_days)
        return (today - timedelta(days=1)).strftime("%Y%m%d")
    except Exception:  # noqa: BLE001 - 出错兜底昨历日
        return (today - timedelta(days=1)).strftime("%Y%m%d")


async def _prev_trading_days(
    tools: ToolProvider, ctx: _Ctx, day: str, lookback: int
) -> list[str]:
    """返回 day 之前 lookback 个交易日（升序）。失败 → []（周涨幅将缺失，诚实降级）。"""
    try:
        d = _parse_ymd(day)
        content = await _call(
            tools,
            ctx,
            "trade_cal",
            {"exchange": "SSE", "start_date": (d - timedelta(days=lookback * 3 + 3)).strftime("%Y%m%d"),
             "end_date": (d - timedelta(days=1)).strftime("%Y%m%d")},
            stage="watchlist",
        )
        rows = _rows_of(content, "watchlist", "trade_cal", ctx)
        open_days = sorted(
            str(_get(r, "cal_date")) for r in rows if str(_get(r, "is_open") or "") in ("1", "1.0")
        )
        return open_days[-lookback:]
    except Exception:  # noqa: BLE001
        return []


async def _api_info(tools: ToolProvider, ctx: _Ctx, api_name: str) -> dict[str, Any] | None:
    """get_api_info 备忘（一次生成只查一次）：拿不到/解析失败/权限 → None（走默认参数兜底尝试）。"""
    if ctx.api_cache.get(api_name, "missing") != "missing":
        return ctx.api_cache[api_name]
    content = await _call(tools, ctx, "get_api_info", {"api_name": api_name}, stage="meta")
    info: dict[str, Any] | None = None
    if content:
        try:
            import json

            body = json.loads(content)
            info = body if isinstance(body, dict) else None
        except (ValueError, TypeError):
            info = None
    ctx.api_cache[api_name] = info
    return info


# ---------------------------------------------------------------------------
# 各段取数（返回 (rows, chain_ok)：chain_ok=至少一次工具调用成功返回）
# ---------------------------------------------------------------------------

async def _fetch_board(tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    chain_ok = False
    # 环1 同花顺概念：ths_index(name=概念) → 指数码 → ths_daily(trade_date=day)
    for concept in cfg.board.th_concepts:
        content = await _call(tools, ctx, "ths_index", {"name": concept, "type": "N"}, stage="board")
        codes = [str(_get(r, "index_code") or _get(r, "ts_code") or "") for r in _extract_rows(content)]
        codes = [c for c in codes if c]
        if not codes:
            continue
        r = await _call(tools, ctx, "ths_daily", {"ts_code": codes[0], "trade_date": day}, stage="board")
        if r is not None:
            chain_ok = True
        for row in _extract_rows(r):
            row["name"] = str(_get(row, "name") or concept)
            row["provider"] = "ths"
            rows.append(row)
    if _is_usable(rows):
        return rows, chain_ok
    # 环2 申万：index_classify(level=…) → sw_daily
    for idx_name in cfg.board.sw_indexes:
        content = await _call(tools, ctx, "index_classify", {"level": "L2", "src": "SW2021"}, stage="board")
        code = ""
        for r in _extract_rows(content):
            if idx_name in str(_get(r, "industry_name") or _get(r, "name") or ""):
                code = str(_get(r, "index_code") or _get(r, "ts_code") or "")
                break
        if not code:
            continue
        r = await _call(tools, ctx, "sw_daily", {"ts_code": code, "start_date": day, "end_date": day}, stage="board")
        if r is not None:
            chain_ok = True
        for row in _extract_rows(r):
            row["name"] = idx_name
            row["provider"] = "sw"
            rows.append(row)
    if _is_usable(rows):
        return rows, chain_ok
    # 环3 核心指数：index_basic（SSE+SZ 两表，实测 399006.SZ 在深市表）→ index_daily
    # 实测（2026-09-05）：上证/沪深300/中证500/创业板指/上证50 均有权且返回真实行情；
    # 申万行业指数（801010.SI）index_daily 返回空、sw_daily 40203 → 不作为环2 的兜底结果。
    basic_maps: dict[str, str] = {}
    for market in ("SSE", "SZ"):
        content = await _call(tools, ctx, "index_basic", {"market": market}, stage="board")
        for r in _extract_rows(content):
            code = str(_get(r, "ts_code") or "")
            name = str(_get(r, "name") or "")
            if code and name and name not in basic_maps:
                basic_maps[name] = code
    for idx_name in cfg.board.indexes:
        code = basic_maps.get(idx_name)
        if not code:
            continue
        r = await _call(tools, ctx, "index_daily", {"ts_code": code, "start_date": day, "end_date": day}, stage="board")
        if r is not None:
            chain_ok = True
        for row in _extract_rows(r):
            row["name"] = idx_name
            row["provider"] = "index"
            rows.append(row)
    return rows, chain_ok


async def _fetch_board_flow(tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str) -> dict[str, float | None]:
    """板块资金流 best-effort：{板块名: 净流入(亿)}；首个「可达」的候选源即停；全部不可达 → {}（记 errors，不阻断）。

    dc_flow=false（默认配置）→ 直接跳过（moneyflow_ind_* 系列实测 40203 无权限，免 4 次失败等待）。
    """
    if not cfg.board.dc_flow:
        return {}
    merged: dict[str, float | None] = {}
    for tool in _FLOW_CANDIDATES:
        content = await _call(tools, ctx, tool, {"trade_date": day}, stage="board")
        if content is None:
            continue
        # 源可达：有行则合并取用，无行（当日无资金流数据）也视为已尝试、不再换源
        for r in _extract_rows(content):
            name = str(_get(r, "name") or _get(r, "板块名称") or "")
            if name and name not in merged:
                merged[name] = _to_yi_num(_get(r, "net_amount"))
        return merged
    return merged


def _to_yi_num(v: Any) -> float | None:
    """资金流净额（**万元**）→ 亿元：/1e4。

    实测教训（2026-09-05 真实数据）：moneyflow.net_mf_amount / moneyflow_ind_dc.net_amount
    均为万元；若沿用 tracker_render._yi_num 的「>=1000 视为万元」启发式，小额（<1000 万）
    会被当成"已亿元"，量级错 4 倍（如光庭信息净流入 993.99万 → 误报 993.99亿）。
    本管线数据源自控，直接用明确单位换算；异常大值（>1e6 万=100亿 ）视为脏数据丢弃。
    """
    if v is None or v == "":
        return None
    try:
        n = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if abs(n) > 1e6:  # 单日主力净流入超 100 亿不可能是真的（单标的），弃
        return None
    return round(n / 1e4, 4)


async def _fetch_watchlist(
    tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str
) -> tuple[dict[str, Any], bool]:
    codes = set(cfg.codes())
    chain_ok = False
    daily_ok = False

    async def snapshot(api: str, on_day: str) -> list[dict[str, Any]]:
        nonlocal chain_ok, daily_ok
        content = await _call(tools, ctx, api, {"trade_date": on_day}, stage="watchlist")
        rows_ = _rows_of(content, "watchlist", api, ctx)
        if content is not None:
            chain_ok = True
            # 主源标志：行情行（pct/close）只来自当日 daily（join 建行），prev-day daily / daily_basic
            # 成功不能代表它 —— daily 失败却被 basic 掩盖会把「断链」报成 empty（2026-09-07 审查）
            if api == "daily" and on_day == day:
                daily_ok = True
        return [r for r in rows_ if str(_get(r, "ts_code") or "") in codes]

    daily_rows = await snapshot("daily", day)
    basic_rows = await snapshot("daily_basic", day)
    prev_days = await _prev_trading_days(tools, ctx, day, 5)
    prev_rows: list[dict[str, Any]] = []
    if prev_days:
        # prev_days 升序（[0]=最远、[-1]=最近前一日）：周涨幅对「5 个交易日前」收盘，
        # 取 [0] 而非 [-1]——2026-09-07 审查：此前用 [-1]（T-2）把 1 日涨跌当成了周涨幅
        prev_rows = await snapshot("daily", prev_days[0])

    names_by_code = {s.ts_code: s.name for s in cfg.watchlist}
    out = join_watchlist(daily_rows, prev_rows, basic_rows, names_by_code, display_limit=cfg.display_limit)
    chain_ok = daily_ok or bool(daily_rows)  # 主源成功或实际有当日行才算链通（否则 na）
    out["_chain_ok"] = chain_ok
    return out, chain_ok


async def _fetch_announce(
    tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str
) -> tuple[list[dict[str, Any]], bool]:
    """类型化组合源（实测权限矩阵 2026-09-05）：anns_d 全部形态 40203 无权限 → **移除主链**，
    改用全部有权限的类型化接口：股东增减持 / 回购 / 大宗交易（+ 业绩类行由 _fetch_forecast 并入）。
    每源独立 try/except（errors 收集）；行为在 pipeline 组装为 {ts_code, ann_date, title, type}，
    projection 直接消费（title 空 → 前端 '—'，诚实）。
    """
    window_start = (_parse_ymd(day) - timedelta(days=7)).strftime("%Y%m%d")
    rows: list[dict[str, Any]] = []
    chain_ok = False

    # 源1: 股东增减持（区间全市场）
    content = await _call(
        tools, ctx, "stk_holdertrade", {"start_date": window_start, "end_date": day}, stage="announce"
    )
    if content is not None:
        chain_ok = True
        for r in _extract_rows(content):
            holder = str(_get(r, "holder_name") or "")
            in_de = str(_get(r, "in_de") or "").upper()
            rows.append({
                "ts_code": str(_get(r, "ts_code") or ""),
                "ann_date": str(_get(r, "ann_date") or ""),
                "title": f"{holder}{'增持' if 'IN' in in_de else '减持'}" if holder else "股东增减持",
                "type": "股东增减持",
            })

    # 源2: 回购（区间全市场）
    content = await _call(tools, ctx, "repurchase", {"start_date": window_start, "end_date": day}, stage="announce")
    if content is not None:
        chain_ok = True
        for r in _extract_rows(content):
            proc = str(_get(r, "proc") or "")
            rows.append({
                "ts_code": str(_get(r, "ts_code") or ""),
                "ann_date": str(_get(r, "ann_date") or ""),
                "title": f"回购（{proc}）" if proc else "回购公告",
                "type": "回购",
            })

    # 源3: 大宗交易（当日全市场）
    content = await _call(tools, ctx, "block_trade", {"trade_date": day}, stage="announce")
    if content is not None:
        chain_ok = True
        for r in _extract_rows(content):
            price = str(_get(r, "price") or "")
            vol = str(_get(r, "vol") or "")
            rows.append({
                "ts_code": str(_get(r, "ts_code") or ""),
                "ann_date": str(_get(r, "trade_date") or day),
                "title": f"大宗交易 {price}元 × {vol}万股" if price else "大宗交易",
                "type": "大宗交易",
            })

    return rows, chain_ok


async def _fetch_forecast(
    tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str
) -> tuple[list[dict[str, Any]], bool]:
    codes = set(cfg.codes())
    rows: list[dict[str, Any]] = []
    chain_ok = False
    for api in ("forecast", "express"):
        # 官方 MCP 实测（2026-09-05）：forecast/express **不支持 start_date/end_date 区间**，
        # 必须 ts_code 或 ann_date（[50101] 参数校验失败）。策略：先 ann_date=day 的「当日全市场快照」
        # （当天披露的预告公告，0-1 次调用、最快），无结果再按池 ts_code 逗号批拉全部（窗口内过滤交给下游）。
        ann_type = "业绩预告" if api == "forecast" else "业绩快报"
        content = await _call(tools, ctx, api, {"ann_date": day}, stage="forecast")
        got = _extract_rows(content) if content is not None else []
        if content is not None:
            chain_ok = True
            for r in got:
                if str(_get(r, "ts_code") or "") in codes:
                    _tag_ann(r, ann_type)
                    rows.append(r)
        # 批拉兜底的门控是「池过滤后为空」——2026-09-07 审查：原先按全市场快照是否为空门控，
        # 任何一天全市场有披露（披露季是常态）就跳过批拉，池内标的在窗口内的披露永远漏报
        if not any(str(_get(r, "ts_code") or "") in codes for r in got):
            for batch in _chunks(cfg.codes(), _BATCH):
                content = await _call(tools, ctx, api, {"ts_code": ",".join(batch)}, stage="forecast")
                if content is not None:
                    chain_ok = True
                    for r in _extract_rows(content):
                        if str(_get(r, "ts_code") or "") in codes:
                            _tag_ann(r, ann_type)
                            rows.append(r)
    return _dedup_latest_forecast(rows, day), chain_ok


def _dedup_latest_forecast(rows: list[dict[str, Any]], day: str, window_days: int = 120) -> list[dict[str, Any]]:
    """同一 ts_code 只保留最近一次披露；披露日（ann_date/notice_date）早于窗口外的记录整体丢弃。

    防批量兜底（ts_code 批量拉取不支持日期参数，只能拿全量后过滤）把陈年历史预告/快报当成
    「本期异动」——报告期字段（scope 展示的 20241231 等）本身仍可以是旧的，只要是最近
    `window_days`（默认 120 天，约一个财季+缓冲）内**披露**的即视为有效。
    """
    cutoff = (_parse_ymd(day) - timedelta(days=window_days)).strftime("%Y%m%d")
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        ann = str(_get(r, "ann_date") or _get(r, "notice_date") or "")
        if ann and ann < cutoff:
            continue
        code = str(_get(r, "ts_code") or "")
        prev = latest.get(code)
        prev_ann = str(_get(prev, "ann_date") or _get(prev, "notice_date") or "") if prev else ""
        if prev is None or ann > prev_ann:
            latest[code] = r
    return list(latest.values())


def _tag_ann(row: dict[str, Any], ann_type: str) -> None:
    """给业绩类行打公告字段标（type/title），供 announcement 段直接投影；title 兜底链：
    归因/摘要 → 类型文本（保证非空，空标题在前端只是 '—'，不如给出类型）。"""
    row.setdefault("type", ann_type)
    row.setdefault(
        "title",
        str(_get(row, "summary") or _get(row, "change_reason") or _get(row, "content") or "") or ann_type,
    )


async def _fetch_news(tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str) -> tuple[list[dict[str, Any]], bool]:
    d = _parse_ymd(day)
    start = f"{d.isoformat()} 00:00:00"
    end = f"{d.isoformat()} 23:59:59"
    reachable = False
    for src in cfg.news_sources:
        content = await _call(tools, ctx, "news", {"src": src, "start_date": start, "end_date": end}, stage="news")
        if content is None:  # 该源不可达（权限/异常）→ 换下一个 src
            continue
        reachable = True
        rows = _extract_rows(content)
        if _is_usable(rows):
            return rows, True
        # 源可达但当日无新闻（非交易日常态）→ 继续看其它 src 是否有
    if reachable:
        return [], True  # 某 src 可达但本期无新闻 → empty（不算未接入）
    # 所有 src 不可达 → Wind 兜底（实测 2026-09-05：Wind 具体工具须经 wind_query(api_name=原始名)
    # 调用，M2 改造后裸名 wind_get_financial_news 会 Unknown；返回 {data:{items:[{content,...}]}}）
    content = await _call(
        tools, ctx, "wind_query",
        {"api_name": "get_financial_news",
         "params": {"query": f"{cfg.name} 算力 光模块 产业链 {d.isoformat()}"}},
        stage="news",
    )
    rows = _unpack_wind_news(content)
    if _is_usable(rows):
        return rows, True
    return [], False


def _unpack_wind_news(content: str | None) -> list[dict[str, Any]]:
    """解包 Wind get_financial_news 返回的 {data:{items:[{title,content,date,url,...}]}} → 新闻行。

    标准 _extract_rows 对 {data:{items}} 只当「单行探针」（1 个 dict 行）不够——专用解包。
    实测教训（2026-09-05 真实数据）：日期字段真名是 `date`，不是 `pub_time`/`publish_time`
    （旧判断字段名错误，`datetime` 一直静默为空）；`title` 字段本身就是真实短标题，优先于
    `content` 截断使用；`url` 是真实存在的 Wind 新闻详情页永久链接。
    """
    if not content:
        return []
    body = _parse_json_dict(content)
    if body is None:
        return []
    data = body.get("data")
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "")
        text = title or str(it.get("content") or "")
        if not text:
            continue
        out.append(
            {
                "title": text if title else text[:80],
                "src": "wind",
                "datetime": str(it.get("date") or it.get("pub_time") or it.get("publish_time") or ""),
                "url": str(it.get("url") or ""),
            }
        )
    return out


def _chunks(items: list[str], n: int) -> list[list[str]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def build_report(
    tools: ToolProvider,
    cfg: WatchlistConfig,
    report_date: str | None = None,
    *,
    stage_timeout: float = 60.0,
    concurrency: int = 4,
) -> dict[str, Any]:
    """取数 + 计算 → 快报 JSON dict（契约见模块 docstring / CLAUDE.md）。

    绝不抛：单段失败/超时/无数据都归一为 status 标志；只有 watchlist 配置缺失这类前置错误由调用方先行检查。
    """
    ctx = _Ctx(asyncio.Semaphore(concurrency), stage_timeout)
    # CompositeToolProvider 的按名路由表（_by_name）只在 list_tools() 时填充——先建立再取数，
    # 否则独立入口（脚本/未走 web 的场景）直接 call_tool 会拿到 "Unknown tool"。
    try:
        await tools.list_tools()
    except Exception as exc:  # noqa: BLE001 - 清单失败不阻断（权限型 provider 的 call 各自降级）
        ctx.error("meta", "list_tools", f"工具清单获取失败：{type(exc).__name__}: {exc}")
    day = await resolve_report_date(tools, ctx, report_date)
    # 日期格式提前校验（六段跑完才在 line 643 崩 = 浪费整轮取数 + 500 而非 422；2026-09-07 审查）
    try:
        day_iso = _parse_ymd(day).isoformat()
    except ValueError as exc:
        raise ConfigError(f"非法报告日期 {day!r}（应为 YYYYMMDD）：{exc}") from exc

    async def stage(name: str, coro: Callable[[], Awaitable[tuple[Any, bool]]]) -> tuple[Any, bool]:
        try:
            return await asyncio.wait_for(coro(), timeout=ctx.stage_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 段级超时/异常 → 该段 na（CancelledError 不在此列）
            ctx.error(name, name, f"阶段超时或异常（{type(exc).__name__}）")
            return {}, False

    async def moneyflow_or_none() -> str | None:
        """moneyflow 快照：纳入段级超时（_call 本身无 wait_for——原先裸 await 不受 60s 保障，2026-09-07 审查）。"""
        try:
            return await asyncio.wait_for(
                _call(tools, ctx, "moneyflow", {"trade_date": day}, stage="board"),
                timeout=ctx.stage_timeout,
            )
        except TimeoutError:
            ctx.error("board", "moneyflow", f"阶段超时（{ctx.stage_timeout:.0f}s）")
            return None

    # 六段 + moneyflow 并行取数（模块 docstring 承诺的 asyncio.gather 语义；Semaphore(concurrency) 在此限流）。
    # 各函数自吞异常（仅 CancelledError 透传），gather 不会因某段失败中断其它段。
    (board_rows, board_ok), flow_map, (watch_raw, watch_ok), (ann_rows, ann_ok), (fc_rows, fc_ok), (news_rows, news_ok), moneyflow = (
        await asyncio.gather(
            stage("board", lambda: _fetch_board(tools, ctx, cfg, day)),
            stage("board_flow", lambda: _fetch_board_flow(tools, ctx, cfg, day)),
            stage("watchlist", lambda: _fetch_watchlist(tools, ctx, cfg, day)),
            stage("announce", lambda: _fetch_announce(tools, ctx, cfg, day)),
            stage("forecast", lambda: _fetch_forecast(tools, ctx, cfg, day)),
            stage("news", lambda: _fetch_news(tools, ctx, cfg, day)),
            moneyflow_or_none(),
        )
    )

    # —— 组装六段（projection 纯计算）——
    names_by_code = {s.ts_code: s.name for s in cfg.watchlist}

    # ① 板块：主行注入资金流（按名匹配），TOP5 用个股资金流（board_flow 的独立结果并入 top_inflow 在 project_board 之后）
    board_rows = _merge_flow(board_rows, flow_map)
    board_proj = project_board(board_rows)
    # 标的池资金流 TOP5：moneyflow 全市场快照 → 池滤 → 降序前 5（best-effort，无数据不阻断）
    top_inflow = _top_stock_inflow(moneyflow, cfg, names_by_code)
    if top_inflow:
        board_proj["top_inflow"] = [
            {"rank": i + 1, "name": n, "inflow": v, "inflow_text": f"{v:.2f}"}
            for i, (n, v) in enumerate(top_inflow)
        ]
    pool_total = _pool_inflow_sum(moneyflow, cfg, names_by_code)
    board_proj["pool_inflow"] = {
        "value": pool_total,
        "text": f"{pool_total:+.2f} 亿" if pool_total is not None else None,
    }

    board_section = _section(
        board_proj, board_rows, board_ok,
        note=None,
    )
    watch_section = _section(
        {k: v for k, v in watch_raw.items() if not k.startswith("_")},
        watch_raw.get("rows", []), watch_ok,
        note=None,
    )
    announce_proj = project_announce(ann_rows, names_by_code)
    # ③ 业绩类行（forecast/express，已打 type/title）并入公告——模板三段过滤即含业绩预告；
    # 仅当公告主链成功（ann_ok）时并入：主链超时/全败（na）时保持 na，预告由第四段单独展示不顶替
    if fc_rows and ann_ok:
        announce_proj = project_announce(list(ann_rows) + fc_rows, names_by_code)
    announce_section = _section(announce_proj, announce_proj["items"], ann_ok, note=None)
    forecast_proj = project_forecast(fc_rows, names_by_code, up=cfg.thresholds.up, down=cfg.thresholds.down)
    forecast_proj["thresholds"] = {"up": cfg.thresholds.up, "down": cfg.thresholds.down}
    forecast_section = _section(forecast_proj, forecast_proj["items"], fc_ok, note=None)
    news_proj = project_news(news_rows)
    news_section = _section(news_proj, news_proj["items"], news_ok,
                            note=None if news_ok else "新闻接口未接入",)

    brief = build_brief(
        board_proj.get("rows", []),
        board_proj.get("top_inflow", []),
        forecast_proj.get("items", []),
        watch_rows=watch_raw.get("rows", []),
        counts={
            "announce": len(announce_proj.get("items", [])),
            "news": len(news_proj.get("items", [])),
        },
    )

    sections = {
        "board": board_section,
        "watchlist": watch_section,
        "announce": announce_section,
        "forecast": forecast_section,
        "news": news_section,
        "brief": {"text": brief["text"], "chars": brief["chars"]},
    }
    missing = [name for name, sec in sections.items() if name != "brief" and sec["status"] == "na"]
    return {
        "version": 1,
        "generated_at": datetime.now(cn_tz()).isoformat(timespec="seconds"),
        "date": day_iso,
        "sector": cfg.name,
        "missing": missing,
        "errors": ctx.errors,
        **sections,
    }


def _section(proj: dict[str, Any], rows: list[dict[str, Any]], chain_ok: bool, *, note: str | None) -> dict[str, Any]:
    """段封装：status = ok（有行）| empty（链成功但本期无）| na（未接入/全败）；note 供前端补充说明。"""
    if rows:
        status = "ok"
    elif chain_ok:
        status = "empty"
    else:
        status = "na"
    return {"status": status, "note": note, **proj}


def _merge_flow(rows: list[dict[str, Any]], flow_map: dict[str, float | None]) -> list[dict[str, Any]]:
    """把板块资金流（亿）按名字并入主行（未知名字忽略；不新增伪板块行）。

    写回 `main_net_inflow`（万元：亿×1e4，_INFLOW 别名首项）——projection._yi_num 会按
    「>=1000 视为万元」口径换回亿元，两条路径统一。
    """
    if not flow_map:
        return rows
    for r in rows:
        name = str(_get(r, "name") or _get(r, "板块名称") or "")
        if name in flow_map and flow_map[name] is not None:
            r["main_net_inflow"] = flow_map[name] * 1e4
    return rows


def _top_stock_inflow(moneyflow_content: str | None, cfg: WatchlistConfig, names: dict[str, str]) -> list[tuple[str, float]]:
    """个股资金流（moneyflow 全市场快照）→ 池滤 → 净流入降序 TOP5：(name, 亿元)。"""
    if not moneyflow_content:
        return []
    rows = _extract_rows(moneyflow_content)
    candidates: list[tuple[str, float]] = []
    for r in rows:
        code = str(_get(r, "ts_code") or "")
        if code not in names:
            continue
        val = _to_yi_num(_get(r, "net_mf_amount"))
        if val is not None:
            candidates.append((names[code], val))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:5]


def _pool_inflow_sum(moneyflow_content: str | None, cfg: WatchlistConfig, names: dict[str, str]) -> float | None:
    """标的池整体资金流合计（亿元）：moneyflow 全市场快照池滤后求和，不截 top5。

    板块段（指数来源）天然没有「主力资金净流入」概念（见 _fetch_board 环3），用这个池级
    汇总代替逐指数展示，复用与 _top_stock_inflow 相同的取值/换算口径，避免两处口径漂移。
    """
    if not moneyflow_content:
        return None
    total: float | None = None
    for r in _extract_rows(moneyflow_content):
        code = str(_get(r, "ts_code") or "")
        if code not in names:
            continue
        val = _to_yi_num(_get(r, "net_mf_amount"))
        if val is not None:
            total = (total or 0.0) + val
    return round(total, 4) if total is not None else None
