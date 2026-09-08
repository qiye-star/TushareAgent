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
from demomcp.quickreport.config import (
    ConfigError,
    IndexSeriesCfg,
    WatchlistConfig,
    cn_tz,
)
from demomcp.quickreport.predicate import news_meta_of, news_title_of, news_url_of
from demomcp.quickreport.projection import (
    build_brief,
    build_index_series,
    flow_to_yi,
    join_watchlist,
    project_announce,
    project_board,
    project_forecast,
    project_news,
)
from demomcp.quickreport.source import (
    KIND_FREE,
    KIND_IFIND,
    KIND_PIPELINE,
    KIND_TUSHARE,
    KIND_WIND,
    source_kind,
    source_label,
)

_PERMISSION_WORDS = ("积分", "权限", "无权限", "提升", "需提高", "需提升", "points")
_BATCH = 30  # 公告/预告逗号批量上限
_FLOW_CANDIDATES = ("moneyflow_ind_dc", "moneyflow_mkt_dc", "moneyflow_cnt_ths", "moneyflow_ind_ths")
# 单只个股单日主力净流入的合理性上界（亿元）：超它视为单位错乱而丢弃。
# 保持 100 亿，**不要收紧**：2026-09-08 实测把它调到 50 亿后，中际旭创当日
# +10.38% 时的 65.48 亿真实净流入被当成脏数据丢掉了（它是当日池内第一），
# 池合计也从 328 亿掉到 262 亿。「A 股没有单票单日破 50 亿」这个假设是错的。
_MAX_STOCK_INFLOW_YI = 100.0


class _Ctx:
    """一次生成的共享状态：并发信号量、错误收集、get_api_info 备忘。"""

    def __init__(self, sem: asyncio.Semaphore, stage_timeout: float) -> None:
        self.sem = sem
        self.stage_timeout = stage_timeout
        self.errors: list[dict[str, str]] = []

    def error(
        self, stage: str, tool: str, reason: str, *, source: str | None = None
    ) -> dict[str, str]:
        """记一条错误并**返回它**——降级链把同一个 reason 引用进 section.attempts，不各写一份。

        `source` 缺省按工具名推断；段级超时这类「不是某个源的错」显式传 KIND_PIPELINE，
        否则一个叫 "announce" 的伪工具名会被误判成 Tushare 接口。
        """
        entry = {
            "stage": stage,
            "tool": tool,
            "reason": reason,
            "source": source or source_kind(tool),
        }
        self.errors.append(entry)
        return entry


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
    # 环3 核心指数：index_basic（沪深两表）→ index_daily
    # 实测（2026-09-05）：上证/沪深300/中证500/创业板指/上证50 均有权且返回真实行情；
    # 申万行业指数（801010.SI）index_daily 返回空、sw_daily 40203 → 不作为环2 的兜底结果。
    #
    # 深市代码必须是 **SZSE**，不是 "SZ"（2026-09-08 实测）：
    #   market="SSE"  → 208 行；market="SZ" → **0 行**；market="SZSE" → 485 行（含创业板指）
    # 直查 index_basic{ts_code:"399006.SZ"} 确认其 market 字段真值就是 "SZSE"。
    # 用 "SZ" 的后果是**静默**丢掉整个创业板指——它 2026-09-07 当日 +3.41%，
    # 是六个宽基里最强的一个，而 errors 里不会有任何痕迹。
    basic_maps: dict[str, str] = {}
    for market in ("SSE", "SZSE"):
        content = await _call(tools, ctx, "index_basic", {"market": market}, stage="board")
        market_rows = _extract_rows(content)
        if content is not None and not market_rows:
            # 不再静默：分类表取回空是「配置的指数可能整段查不到」的前兆，必须留痕
            ctx.error("board", "index_basic", f"指数分类表 market={market} 返回 0 行")
        for r in market_rows:
            code = str(_get(r, "ts_code") or "")
            name = str(_get(r, "name") or "")
            if code and name and name not in basic_maps:
                basic_maps[name] = code
    for idx_name in cfg.board.indexes:
        code = basic_maps.get(idx_name)
        if not code:
            ctx.error("board", "index_basic", f"指数「{idx_name}」在分类表里未命中，本段跳过")
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
                merged[name] = flow_to_yi(_get(r, "net_amount"))
        return merged
    return merged


async def _fetch_board_series(
    tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str
) -> tuple[list[dict[str, Any]], bool]:
    """指数走势序列（给前端折线图）：每条配置一次取数，降级链 Tushare -> AkShare。

    主源 Tushare `index_daily(ts_code, start_date, end_date)`：与板块段**同源同口径**
    （board 行的 pct 就来自它）、列里自带 pct_chg、已有权限、不依赖免费源进程存活。
    实测返回**裸数组**（不是 {code,msg,data} 信封）且**降序**（最新在前）。

    兜底 AkShare `get_index_data(index_code=6 位裸码)`（新浪源，不受东财行情 CDN 的 IP 封锁）：
    实测 500 行 OHLCV，收盘价与 Tushare 逐位吻合（4575.0245 vs 4575.025），可互为交叉验证。

    `board.series` 默认为空 -> 一条都不配就不会产生任何调用（现有测试 fixture 零影响）。
    """
    if not cfg.board.series:
        return [], False
    d = _parse_ymd(day)
    # 多取一些自然日：120 个交易日约 170 个自然日，再放宽到 1.9 倍防长假
    start = (d - timedelta(days=int(cfg.board.series_days * 1.9) + 10)).strftime("%Y%m%d")
    out: list[dict[str, Any]] = []
    chain_ok = False
    for item in cfg.board.series:
        rows = await _series_rows_tushare(tools, ctx, item, start, day)
        if rows:
            chain_ok = True
        else:
            rows = await _series_rows_akshare(tools, ctx, item)
            if rows:
                chain_ok = True
        if not rows:
            continue
        out.append(build_index_series(item.name, item.code, rows, days=cfg.board.series_days))
    return out, chain_ok


async def _series_rows_tushare(
    tools: ToolProvider, ctx: _Ctx, item: IndexSeriesCfg, start: str, end: str
) -> list[dict[str, Any]]:
    content = await _call(
        tools, ctx, "index_daily",
        {"ts_code": item.code, "start_date": start, "end_date": end},
        stage="board_series",
    )
    return _extract_rows(content) if content is not None else []


async def _series_rows_akshare(
    tools: ToolProvider, ctx: _Ctx, item: IndexSeriesCfg
) -> list[dict[str, Any]]:
    """免费兜底：AkShare 要 6 位裸码（`000300.SH` -> `000300`），且总是给 tail(500)。"""
    bare = item.code.split(".")[0]
    content = await _call(
        tools, ctx, "get_index_data", {"index_code": bare}, stage="board_series"
    )
    return _extract_rows(content) if content is not None else []


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


async def _fetch_news(
    tools: ToolProvider, ctx: _Ctx, cfg: WatchlistConfig, day: str
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    """产业链催化事件：四级降级链。返回 (rows, chain_ok, prov)。

    **顺序由 2026-09-08 实测确定，与改造前正好相反**：
      ① 免费源 china_news（get_market_headlines / 可选 get_stock_news）
         —— 免费、无配额，且东财的**新闻域**不在行情 CDN 的 IP 封锁范围内（实测始终可用）
      ② iFind search_news —— 结构化记录，受套餐并发限制（最多用 1 次调用）
      ③ Wind get_financial_news —— 实测当日报「单日请求次数超限」（有日配额，不是没权限）
      ④ Tushare news(src=…) —— 实测两个 src 全 40203 无权限

    改造前的顺序是 ④→③：每轮先烧两次必失败的 Tushare 调用，再撞上 Wind 日配额，才落到 na。
    倒转顺序既是正确性修复也是延迟修复。

    三态语义不变：reachable 跨层累积 → 有行=ok / 可达但无行=empty / 全层不可达=na。
    每层命中即短路返回（第一手拿到就不再打后面的付费源）。
    """
    d = _parse_ymd(day)
    prov: dict[str, Any] = {"src": None, "src_tool": None, "attempts": [], "note": None}
    reachable = False

    def _attempt(kind: str, tool: str, *, ok: bool, reason: str | None = None) -> None:
        prov["attempts"].append(
            {"source": kind, "tool": tool, "ok": ok, **({"reason": reason} if reason else {})}
        )

    def _win(kind: str, tool: str, note: str | None = None) -> None:
        prov["src"], prov["src_tool"] = kind, tool
        if note:
            prov["note"] = note
        _attempt(kind, tool, ok=True)

    # —— ① 免费源：市场头条（+ 可选个股新闻）——
    content = await _call(tools, ctx, "get_market_headlines", {"top_n": 50}, stage="news")
    if content is not None:
        reachable = True
        rows, filtered = _unpack_free_headlines(content, cfg.news_keywords)
        extra: list[dict[str, Any]] = []
        for code in cfg.news_stock_codes:
            c = await _call(tools, ctx, "get_stock_news", {"ticker": code}, stage="news")
            if c is None:
                _attempt(KIND_FREE, "get_stock_news", ok=False, reason="取数失败")
                continue
            _attempt(KIND_FREE, "get_stock_news", ok=True)
            extra.extend(_unpack_free_stock_news(c))
        merged = _dedup_news(rows + extra)
        if _is_usable(merged):
            note = None if (filtered or not cfg.news_keywords) else "无产业链关键词命中，展示市场头条"
            _win(KIND_FREE, "get_market_headlines", note)
            return merged, True, prov
        _attempt(KIND_FREE, "get_market_headlines", ok=True, reason="可达但本期无可用条目")
    else:
        _attempt(KIND_FREE, "get_market_headlines", ok=False, reason="取数失败")

    # —— ② iFind search_news（最多 1 次调用，不触碰 IFIND_CONCURRENCY=2）——
    content = await _call(
        tools, ctx, "ifind_query",
        {"api_name": "search_news",
         "params": {"query": _news_query(cfg),
                    "time_start": (d - timedelta(days=2)).isoformat(),
                    "time_end": d.isoformat(),
                    "size": 20}},
        stage="news",
    )
    if content is not None:
        reachable = True
        rows = _unpack_ifind_news(content)
        if _is_usable(rows):
            _win(KIND_IFIND, "ifind_query")
            return rows, True, prov
        _attempt(KIND_IFIND, "ifind_query", ok=True, reason="可达但未返回结构化条目")
    else:
        _attempt(KIND_IFIND, "ifind_query", ok=False, reason="取数失败")

    # —— ③ Wind 兜底（实测：具体工具须经 wind_query(api_name=原始名)，裸名会 Unknown）——
    content = await _call(
        tools, ctx, "wind_query",
        {"api_name": "get_financial_news",
         "params": {"query": f"{cfg.name} 算力 光模块 产业链 {d.isoformat()}"}},
        stage="news",
    )
    if content is not None:
        reachable = True
        rows = _unpack_wind_news(content)
        if _is_usable(rows):
            _win(KIND_WIND, "wind_query")
            return rows, True, prov
        _attempt(KIND_WIND, "wind_query", ok=True, reason="可达但本期无条目")
    else:
        _attempt(KIND_WIND, "wind_query", ok=False, reason="取数失败")

    # —— ④ Tushare news（实测全 40203，放最后）——
    start = f"{d.isoformat()} 00:00:00"
    end = f"{d.isoformat()} 23:59:59"
    for src in cfg.news_sources:
        content = await _call(
            tools, ctx, "news", {"src": src, "start_date": start, "end_date": end}, stage="news"
        )
        if content is None:
            _attempt(KIND_TUSHARE, "news", ok=False, reason=f"src={src} 取数失败")
            continue
        reachable = True
        rows = _unpack_tushare_news(content)
        if _is_usable(rows):
            _win(KIND_TUSHARE, "news")
            return rows, True, prov
        _attempt(KIND_TUSHARE, "news", ok=True, reason=f"src={src} 可达但本期无条目")

    # 某层可达但都无内容 → empty（不算未接入）；全层不可达 → na
    return [], reachable, prov


def _news_query(cfg: WatchlistConfig) -> str:
    """iFind 的自然语言检索串：行业名 + 关键词（iFind 只吃一个 query，不是结构化字段）。"""
    kw = " ".join(cfg.news_keywords[:6]) if cfg.news_keywords else "算力 光模块 产业链"
    return f"{cfg.name} {kw}".strip()


def _news_row(title: str, src: str, dt: str, url: str) -> dict[str, Any]:
    """归一化的新闻行——**四层解包器的统一输出形状**。

    统一形状是刻意的：下游 project_news 就不必再去猜各源五花八门的列名
    （标题 / 资讯标题 / 新闻标题…），别名劫持风险从源头消失。
    """
    return {"title": title, "src": src, "datetime": dt, "url": url}


def _unpack_free_headlines(
    content: str, keywords: tuple[str, ...]
) -> tuple[list[dict[str, Any]], bool]:
    """免费源市场头条（裸数组，列 标题/摘要/发布时间/链接）→ (归一行, 是否命中过关键词)。

    get_market_headlines 是**全市场**头条：实测同一批返回里既有
    「华丰科技：224G 等高速背板连接器产品已开始批量交付」（相关），
    也有「《全国渔业发展十五五规划》印发」（完全无关）。所以必须按关键词过滤。
    一条都不命中时**退回最新 10 条并由调用方写 note**——诚实说明「没有产业链命中」，
    比假装「本期无催化」要好（后者会被读成「今天产业链没事发生」）。
    """
    out: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    for r in _extract_rows(content):
        title = news_title_of(r)
        if not title:
            continue
        src, dt = news_meta_of(r)
        row = _news_row(title, src or "东方财富快讯", dt, news_url_of(r))
        out.append(row)
        if keywords:
            blob = title + str(_get(r, "摘要") or "")
            if any(k and k in blob for k in keywords):
                hits.append(row)
    if not keywords:
        return out[:20], False
    return (hits[:20], True) if hits else (out[:10], False)


def _unpack_free_stock_news(content: str) -> list[dict[str, Any]]:
    """免费源个股新闻（列 关键词/新闻标题/新闻内容/发布时间/文章来源/新闻链接）→ 归一行。"""
    out: list[dict[str, Any]] = []
    for r in _extract_rows(content)[:10]:
        title = news_title_of(r)
        if not title:
            continue
        src, dt = news_meta_of(r)
        out.append(_news_row(title, src or "东方财富", dt, news_url_of(r)))
    return out


def _unpack_ifind_news(content: str | None) -> list[dict[str, Any]]:
    """iFind search_news → 归一新闻行。

    实测信封是**双重编码**：外层 code/msg/data，data 里还有一层 data 是 JSON 数组**字符串**，
    内层记录列为 资讯标题 / 资讯内容 / 日期 / URL。标准 _extract_rows 对它只当
    「单行探针」（返回 1 行、且那一行的唯一键是尚未解码的 data 字符串）→ 必须专用解包，
    定位同 _unpack_wind_news。

    若 data 只有 answer（markdown 表格或「未返回有效结果」提示）→ 返回 []，
    诚实降级到下一层，不去解析 markdown（解析不确定，宁可换源）。
    """
    body = _parse_json_dict(content or "")
    if body is None:
        return []
    data: Any = body.get("data")
    if isinstance(data, str):
        data = _parse_json_any(data)
    if isinstance(data, dict):
        inner = data.get("data")
        data = _parse_json_any(inner) if isinstance(inner, str) else inner
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for it in data[:20]:
        if not isinstance(it, dict):
            continue
        title = news_title_of(it)
        if not title:
            continue
        src, dt = news_meta_of(it)
        out.append(_news_row(title, src or "同花顺iFind", dt, news_url_of(it)))
    return out


def _unpack_tushare_news(content: str) -> list[dict[str, Any]]:
    """Tushare news → 归一新闻行（该源的行本就带 src/datetime/title 列）。"""
    out: list[dict[str, Any]] = []
    for r in _extract_rows(content)[:20]:
        title = news_title_of(r)
        if not title:
            continue
        src, dt = news_meta_of(r)
        out.append(_news_row(title, src, dt, news_url_of(r)))
    return out


def _dedup_news(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按标题前 40 字去重、保序（头条与个股新闻常有同一条）。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        key = str(r.get("title") or "")[:40]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _parse_json_any(text: str) -> Any:
    """宽松 JSON 解析（可能是数组也可能是对象）；失败 → None。"""
    try:
        import json

        return json.loads(text)
    except (ValueError, TypeError):
        return None


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

    async def stage(
        name: str,
        coro: Callable[[], Awaitable[Any]],
        *,
        fallback: tuple[Any, ...],
    ) -> Any:
        """段级超时/异常 → 返回 `fallback`（CancelledError 不在此列，照常透传）。

        `fallback` 是**该段的整个返回值**，元数与类型都要与正常路径一致：
        行列表段 `([], False)`、dict 段 `({}, False)`、带 provenance 的 news 段 `([], False, {})`。
        原先一律返回 `({}, False)`，list 段拿到空 dict 只是「恰好」不炸
        （`for r in {}` 什么也不产、`list({}) == []`），任何切片/索引消费者都会崩；
        而 news 段改成三元返回后，二元 fallback 会直接在解包处 ValueError。
        """
        try:
            return await asyncio.wait_for(coro(), timeout=ctx.stage_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 段级超时/异常 → 该段 na（CancelledError 不在此列）
            ctx.error(name, name, f"阶段超时或异常（{type(exc).__name__}）", source=KIND_PIPELINE)
            return fallback

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
    (
        (board_rows, board_ok),
        flow_map,
        (watch_raw, watch_ok),
        (ann_rows, ann_ok),
        (fc_rows, fc_ok),
        (news_rows, news_ok, news_prov),
        (series_rows, series_ok),
        moneyflow,
    ) = (
        await asyncio.gather(
            stage("board", lambda: _fetch_board(tools, ctx, cfg, day), fallback=([], False)),
            stage("board_flow", lambda: _fetch_board_flow(tools, ctx, cfg, day), fallback=({}, False)),
            stage("watchlist", lambda: _fetch_watchlist(tools, ctx, cfg, day), fallback=({}, False)),
            stage("announce", lambda: _fetch_announce(tools, ctx, cfg, day), fallback=([], False)),
            stage("forecast", lambda: _fetch_forecast(tools, ctx, cfg, day), fallback=([], False)),
            stage("news", lambda: _fetch_news(tools, ctx, cfg, day), fallback=([], False, {})),
            # 独立成段级预算：新浪/Tushare 的走势取数慢不能拖垮板块环
            stage("board_series", lambda: _fetch_board_series(tools, ctx, cfg, day), fallback=([], False)),
            moneyflow_or_none(),
        )
    )

    # —— 组装六段（projection 纯计算）——
    names_by_code = {s.ts_code: s.name for s in cfg.watchlist}

    # ① 板块：主行注入资金流（按名匹配），TOP5 用个股资金流（board_flow 的独立结果并入 top_inflow 在 project_board 之后）
    board_rows = _merge_flow(board_rows, flow_map)
    board_proj = project_board(board_rows)
    # 标的池资金流 TOP5：moneyflow 全市场快照 → 池滤 → 降序前 5（best-effort，无数据不阻断）
    top_inflow = _top_stock_inflow(moneyflow, names_by_code)
    if top_inflow:
        # 个股级 TOP5 会顶掉 project_board 算出的板块级列表——把后者保留下来而不是丢掉，
        # 字段名各自诚实（`top_inflow` 现在装的是个股，`sector_inflow_top` 才是板块）。
        board_proj["sector_inflow_top"] = board_proj.get("top_inflow", [])
        board_proj["top_inflow"] = [
            {"rank": i + 1, "name": n, "inflow": v, "inflow_text": _fmt_yi(v)}
            for i, (n, v) in enumerate(top_inflow)
        ]
    board_proj["pool_inflow"] = _pool_inflow_sum(moneyflow, names_by_code)
    # 指数走势序列：**board 的子键而不是第七段**。第七段会自动进顶层 missing（见下方 missing 推导），
    # 且序列取数失败不该把整个板块段翻成「未接入」——两件事的严重性不同。
    board_proj["series"] = series_rows
    board_proj["series_status"] = "ok" if series_rows else ("empty" if series_ok else "na")

    board_section = _section(
        board_proj, board_rows, board_ok,
        note=None,
        prov=_prov_of(board_rows, "provider", {"ths": "ths_daily", "sw": "sw_daily", "index": "index_daily"}),
    )
    watch_section = _section(
        {k: v for k, v in watch_raw.items() if not k.startswith("_")},
        watch_raw.get("rows", []), watch_ok,
        note=None,
        prov={"src": KIND_TUSHARE, "src_tool": "daily"} if watch_ok else {},
    )
    announce_proj = project_announce(ann_rows, names_by_code)
    # ③ 业绩类行（forecast/express，已打 type/title）并入公告——模板三段过滤即含业绩预告；
    # 仅当公告主链成功（ann_ok）时并入：主链超时/全败（na）时保持 na，预告由第四段单独展示不顶替
    if fc_rows and ann_ok:
        announce_proj = project_announce(list(ann_rows) + fc_rows, names_by_code)
    announce_section = _section(
        announce_proj, announce_proj["items"], ann_ok, note=None,
        prov={"src": KIND_TUSHARE, "src_tool": "stk_holdertrade/repurchase/block_trade"} if ann_ok else {},
    )
    forecast_proj = project_forecast(fc_rows, names_by_code, up=cfg.thresholds.up, down=cfg.thresholds.down)
    forecast_proj["thresholds"] = {"up": cfg.thresholds.up, "down": cfg.thresholds.down}
    forecast_section = _section(
        forecast_proj, forecast_proj["items"], fc_ok, note=None,
        prov={"src": KIND_TUSHARE, "src_tool": "forecast/express"} if fc_ok else {},
    )
    news_proj = project_news(news_rows)
    news_section = _section(
        news_proj, news_proj["items"], news_ok,
        note=None if news_ok else "新闻接口未接入",
        prov=news_prov,
    )

    brief = build_brief(
        board_proj.get("rows", []),
        board_proj.get("top_inflow", []),
        forecast_proj.get("items", []),
        # **未截断**的全量行：`rows` 已按 display_limit 截断（211 只的池子只剩 100 只），
        # 用它统计会让「标的池 N 涨 M 跌」「涨幅居前 TOP2」只覆盖池子的前 100 只
        # （且是 watchlist.json 原始顺序、不是涨跌幅排序）。`_` 前缀键在 line 上方被剥掉、不进契约。
        watch_rows=watch_raw.get("_all_rows") or watch_raw.get("rows", []),
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


def _prov_of(
    rows: list[dict[str, Any]], key: str, tool_by_value: dict[str, str]
) -> dict[str, Any]:
    """从行里的来源标记（如 board 行的 `provider`）反推该段实际由哪个工具服务。

    board 段是三环降级链，哪一环命中只有行自己知道（`provider` ∈ ths/sw/index）——
    与其在取数函数里再串一路返回值，不如从已有的行标记反推。取首行的标记：
    同一段的行必然来自同一环（每环命中即 return）。
    """
    for r in rows:
        val = str(r.get(key) or "")
        if val in tool_by_value:
            return {"src": KIND_TUSHARE, "src_tool": tool_by_value[val]}
    return {}


def _section(
    proj: dict[str, Any],
    rows: list[dict[str, Any]],
    chain_ok: bool,
    *,
    note: str | None,
    prov: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """段封装：status = ok（有行）| empty（链成功但本期无）| na（未接入/全败）；note 供前端补充说明。

    `prov` = 该段的取数溯源（`src` 源类别 / `src_tool` 实际工具名 / `src_label` 展示名 /
    `attempts` 按序尝试记录）。**随报文归档**是刻意的：报文按日存档、前端支持历史回看，
    而 /status 那种「实时」视角看历史报告会把今天的来源贴到上周的数据上。
    `fetched_at` 让看板能显示「这一段是什么时候取的」。
    """
    if rows:
        status = "ok"
    elif chain_ok:
        status = "empty"
    else:
        status = "na"
    prov = dict(prov or {})
    src = prov.get("src")
    return {
        "status": status,
        "note": note or prov.get("note"),
        "src": src,
        "src_tool": prov.get("src_tool"),
        "src_label": source_label(src) if src else None,
        "attempts": prov.get("attempts", []),
        "fetched_at": datetime.now(cn_tz()).isoformat(timespec="seconds"),
        **proj,
    }


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


def _fmt_yi(v: float) -> str:
    """已换算为**亿元**的数值 → 展示串。两条 top_inflow 路径共用一份格式化。

    刻意不用 tracker_render._to_yi：那个会先跑 `_yi_num` 的「>=1000 视为万元」启发式，
    对已经是亿元的值构成**二次换算**（≥1000 亿时会被再除 1e4）。
    """
    return f"{v:.2f}"


def _top_stock_inflow(moneyflow_content: str | None, names: dict[str, str]) -> list[tuple[str, float]]:
    """个股资金流（moneyflow 全市场快照）→ 池滤 → 净流入降序 TOP5：(name, 亿元)。"""
    if not moneyflow_content:
        return []
    rows = _extract_rows(moneyflow_content)
    candidates: list[tuple[str, float]] = []
    for r in rows:
        code = str(_get(r, "ts_code") or "")
        if code not in names:
            continue
        val = flow_to_yi(_get(r, "net_mf_amount"), max_yi=_MAX_STOCK_INFLOW_YI)
        if val is not None:
            candidates.append((names[code], val))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:5]


def _pool_inflow_sum(
    moneyflow_content: str | None, names: dict[str, str]
) -> dict[str, Any]:
    """标的池整体资金流合计（亿元）→ {value, text, count, dropped, suspect}。

    板块段（指数来源）天然没有「主力资金净流入」概念（见 _fetch_board 环3），用这个池级
    汇总代替逐指数展示，复用与 _top_stock_inflow 相同的取值/换算口径，避免两处口径漂移。

    **刻意不给合计封顶**：丢掉一个真实的 +328 亿净流入日，比把它显示出来更糟
    （违背本模块「缺则标未接入、绝不编造」但也绝不隐瞒的原则）。合计改为**可审计**：
    - `count` = 参与合计的行数、`dropped` = 被逐条守卫丢掉的行数；
    - `incomplete = dropped > 0` —— 这是**事实**而非猜测：确实有行没被计入，合计因此偏低，
      前端据此标注而不是默默呈现一个不完整的和。

    这里刻意**没有**「合计超过某个亿元阈值就算可疑」的判定：一个 211 只标的的产业链池
    在普涨日（实测 137 涨 / 均幅 +2.12%）合计 262 亿完全正常，任何这类阈值都会在正常日误报。
    宁可不给信号，也不给假信号。
    """
    if not moneyflow_content:
        return {"value": None, "text": None, "count": 0, "dropped": 0, "incomplete": False}
    total: float | None = None
    count = 0
    dropped = 0
    for r in _extract_rows(moneyflow_content):
        code = str(_get(r, "ts_code") or "")
        if code not in names:
            continue
        raw = _get(r, "net_mf_amount")
        val = flow_to_yi(raw, max_yi=_MAX_STOCK_INFLOW_YI)
        if val is None:
            if raw is not None and raw != "":
                dropped += 1
            continue
        total = (total or 0.0) + val
        count += 1
    value = round(total, 4) if total is not None else None
    return {
        "value": value,
        "text": f"{value:+.2f} 亿" if value is not None else None,
        "count": count,
        "dropped": dropped,
        "incomplete": dropped > 0,
    }
