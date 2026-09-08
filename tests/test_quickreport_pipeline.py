"""quickreport 取数管线：fake provider 驱动——降级链 / 阈值 / 权限 / 段级超时 / errors / 日期解析。"""

from __future__ import annotations

import asyncio
import json

from demomcp.interfaces.types import ToolResult
from demomcp.quickreport.config import WatchlistConfig
from demomcp.quickreport.pipeline import (
    _biz_fail_reason,
    _dedup_latest_forecast,
    _pool_inflow_sum,
    build_report,
    resolve_report_date,
)


def _cfg(tmp_path, **overrides) -> WatchlistConfig:
    data = {
        "watchlist": [
            {"name": "新易盛", "ts_code": "300502.SZ"},
            {"name": "天孚通信", "ts_code": "300394.SZ"},
        ],
        "board": {"th_concepts": [], "sw_indexes": [], "indexes": ["上证指数", "创业板指"], "dc_flow": False},
        "thresholds": {"up": 50.0, "down": -20.0},
        "schedule": {"hour": 8, "minute": 30},
        "news_sources": ["wallstreetcn", "sina"],
        "display_limit": 100,
    }
    data.update(overrides)
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cfg = WatchlistConfig.load(p)
    assert cfg is not None
    return cfg


class FakeTools:
    """按工具名路由的假 provider：handlers 缺失的工具 → 未知工具错误。"""

    def __init__(self, handlers: dict[str, object], *, fail_fields: set[str] | None = None) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, dict]] = []
        self.fail_fields = fail_fields or set()

    async def list_tools(self):
        return []

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        self.calls.append((name, arguments or {}))
        h = self.handlers.get(name)
        if h is None:
            return ToolResult(content="Unknown tool", is_error=True)
        if callable(h):
            out = h(name, arguments or {})
            if asyncio.iscoroutine(out):
                out = await out
            return out
        return ToolResult(content=h, is_error=False)


def _ok(rows: list[dict]) -> str:
    return json.dumps({"code": 0, "msg": "", "row_count": len(rows), "data": rows}, ensure_ascii=False)


def _bare(rows: list[dict]) -> str:
    """免费源（external_sources）的成功形态：**裸 records 数组**，没有 Tushare 的 code/msg 信封。"""
    return json.dumps(rows, ensure_ascii=False)


def _free_err(tool: str) -> str:
    """免费源的失败形态：{"error": "..."}，且 is_error=False（业务结果）。

    这个形状必须被判成失败——它既没有 code 字段（旧判据会当成功），
    又会被 _extract_rows 当成一行数据（信封探测不认 error 键）。
    """
    return json.dumps({"error": f"{tool} failed: curl exit 56"}, ensure_ascii=False)


def _day_rows(code, close, pct_chg=None, trade_date="20260904"):
    r = {"ts_code": code, "close": close, "trade_date": trade_date}
    if pct_chg is not None:
        r["pct_chg"] = pct_chg
    return r


def _basic_row(code, turnover=4.5, total_mv=6953000):
    return {"ts_code": code, "turnover_rate": turnover, "total_mv": total_mv}


def _happy_handlers():
    """六段全通的 handlers（核心指数行情 + 类型化公告三源 + 快照 + 预告 + 新闻 + 个股资金流）。"""
    return {
        "trade_cal": lambda n, a: ToolResult(
            _ok([{"exchange": "SSE", "cal_date": "20260904", "is_open": "1"},
                 {"exchange": "SSE", "cal_date": "20260905", "is_open": "1"}]),
            is_error=False,
        ),
        # 环3 核心指数：index_basic(SSE/SZ) 名称匹配 → index_daily
        "index_basic": lambda n, a: ToolResult(
            _ok([{"ts_code": "000001.SH", "name": "上证指数"},
                 {"ts_code": "399006.SZ", "name": "创业板指"}]),
            is_error=False,
        ),
        "index_daily": lambda n, a: ToolResult(
            _ok([{"ts_code": a.get("ts_code"), "trade_date": "20260904", "close": 4000.0, "pct_chg": 0.85}]),
            is_error=False,
        ),
        "daily": lambda n, a: ToolResult(
            _ok([_day_rows("300502.SZ", 100.0, 5.67, a.get("trade_date")),
                 _day_rows("300394.SZ", 50.0, -1.0, a.get("trade_date"))]),
            is_error=False,
        ),
        "daily_basic": lambda n, a: ToolResult(_ok([_basic_row("300502.SZ"), _basic_row("300394.SZ")]), is_error=False),
        "moneyflow": lambda n, a: ToolResult(
            _ok([{"ts_code": "300502.SZ", "net_mf_amount": 32000}, {"ts_code": "300394.SZ", "net_mf_amount": 5000}]),
            is_error=False,
        ),
        # 类型化公告三源
        "stk_holdertrade": lambda n, a: ToolResult(
            _ok([{"ts_code": "300502.SZ", "ann_date": "20260904", "holder_name": "控股股东", "in_de": "IN"}]),
            is_error=False,
        ),
        "repurchase": lambda n, a: ToolResult(
            _ok([{"ts_code": "300394.SZ", "ann_date": "20260903", "proc": "实施"}]),
            is_error=False,
        ),
        "block_trade": lambda n, a: ToolResult(
            _ok([{"ts_code": "300502.SZ", "trade_date": "20260904", "price": 99.5, "vol": 120.0}]),
            is_error=False,
        ),
        "forecast": lambda n, a: ToolResult(
            _ok([{"ts_code": "300502.SZ", "period": "20260930", "p_change_min": 60.0, "p_change_max": 110.0,
                  "change_reason": "算力需求高增"},
                 {"ts_code": "300394.SZ", "period": "20260930", "p_change_min": 30.0, "p_change_max": 40.0}]),
            is_error=False,
        ),
        "express": lambda n, a: ToolResult(_ok([]), is_error=False),
        # 催化事件第①层（免费源 china_news）：**裸数组**，列名是中文的 标题/摘要/发布时间/链接
        # （实测形态）。放在 handlers 里同时锁住两件事：四级链的顺序（这一层命中就不该再打
        # iFind/Wind/Tushare），以及 predicate._NEWS_TITLE 认得 `标题`（tracker_render._NEWS 认不得）。
        "get_market_headlines": lambda n, a: ToolResult(
            _bare([{"标题": "英伟达宣布量产", "摘要": "光模块需求高增",
                    "发布时间": "2026-09-04 09:00:00",
                    "链接": "https://finance.eastmoney.com/a/1.html"}]),
            is_error=False,
        ),
        "news": lambda n, a: ToolResult(
            _ok([{"title": "英伟达宣布量产", "src": a.get("src"), "datetime": "2026-09-04 09:00:00"}]), is_error=False
        ),
    }


async def test_full_success_six_sections(tmp_path) -> None:
    tools = FakeTools(_happy_handlers())
    report = await build_report(tools, _cfg(tmp_path), report_date="20260904", stage_timeout=20.0, concurrency=4)
    assert report["date"] == "2026-09-04"
    assert report["missing"] == []
    assert report["errors"] == []
    # ① 板块：核心指数环3（index_basic SSE+SZ → index_daily）
    assert report["board"]["status"] == "ok"
    assert report["board"]["rows"][0]["name"] == "上证指数"
    assert report["board"]["rows"][0]["provider"] == "index"
    assert report["board"]["rows"][0]["pct_text"] == "+0.85%"
    assert report["board"]["rows"][0]["inflow"] is None  # dc_flow=false → 板块资金流跳过
    assert report["board"]["top_inflow"][0]["name"] == "新易盛"  # 个股资金流 TOP1
    assert report["board"]["pool_inflow"]["value"] == 3.7  # 3.2 + 0.5 亿（新易盛+天孚通信）
    assert report["board"]["pool_inflow"]["text"] == "+3.70 亿"
    assert report["board"]["top_inflow"][0]["inflow_text"] == "3.20"  # 32000 万 → 3.2 亿
    # ② 标的池
    assert report["watchlist"]["status"] == "ok"
    assert report["watchlist"]["pool_count"] == 2
    assert report["watchlist"]["rows"][0]["name"] == "新易盛"
    # ③ 公告：类型化三源 + 业绩类并入
    assert report["announce"]["status"] == "ok"
    ann_types = {i["type"] for i in report["announce"]["items"]}
    assert {"股东增减持", "回购", "大宗交易", "业绩预告"} <= ann_types
    assert all(i["title"] for i in report["announce"]["items"])  # title 均非空
    # ④ 预告（只保留触发者）
    assert report["forecast"]["status"] == "ok" and report["forecast"]["hit_count"] == 1
    assert report["forecast"]["items"][0]["name"] == "新易盛"
    # ⑤ 新闻
    assert report["news"]["status"] == "ok"
    assert "英伟达" in report["news"]["items"][0]["title"]
    # ⑥ 研判：素材扩充（指数涨跌/资金流/池内统计/涨幅居前/预告/公告计数）
    assert "上证指数" in report["brief"]["text"]
    assert "标的池 1 涨 1 跌" in report["brief"]["text"]
    assert "涨幅居前" in report["brief"]["text"]
    assert "公告" in report["brief"]["text"]
    assert report["brief"]["chars"] >= 70 and report["brief"]["chars"] <= 150


async def test_board_index_source_failure_then_na(tmp_path) -> None:
    """核心指数链失败（index_basic 不可达 + 名称匹配空）→ board na + errors（其它段照常）。"""
    handlers = _happy_handlers()
    handlers["index_basic"] = lambda n, a: ToolResult("ERR", is_error=True)
    handlers["index_daily"] = lambda n, a: ToolResult("ERR", is_error=True)
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["board"]["status"] == "na"
    assert "board" in report["missing"]

    # 名称匹配不到（index_basic 返回但配置名不在表里）→ 无行 → na
    handlers2 = _happy_handlers()
    handlers2["index_basic"] = lambda n, a: ToolResult(_ok([{"ts_code": "000001.SH", "name": "上证全指"}]), is_error=False)
    report2 = await build_report(FakeTools(handlers2), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report2["board"]["status"] == "na"


async def test_news_permission_then_wind_then_na(tmp_path) -> None:
    """降到 Wind 层：wind_query 兜底成功（解包 items）；无 Wind → na + missing。

    必须先摘掉第①层（免费源）与第②层（iFind）的 handler，否则四级链在第①层就命中、
    这个测试再也测不到 Wind——顺序改造后 Wind 是第③层。
    """
    handlers = _happy_handlers()
    del handlers["get_market_headlines"]  # ① 免费源缺席
    handlers["news"] = lambda n, a: ToolResult(
        "{\"code\": 40203, \"msg\": \"积分不足或无权限，请提升积分\"}", is_error=False
    )
    handlers["wind_query"] = lambda n, a: ToolResult(
        json.dumps({"data": {"items": [{
            "title": "英伟达 Spectrum-X 进入全面量产阶段，光模块景气延续",
            "content": "（正文全文，比 title 长很多……）",
            "date": "2026-09-04",
            "url": "https://t.wind.com.cn/mobwftweb/M/news.html?code=ABC123",
        }]}}, ensure_ascii=False),
        is_error=False,
    )
    tools = FakeTools(handlers)
    report = await build_report(tools, _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["news"]["status"] == "ok"
    assert report["news"]["items"][0]["src"] == "wind"
    assert "光模块" in report["news"]["items"][0]["title"]
    # 真实字段名回归：优先 title（不截断）而非 content 截 80；date 而非 pub_time/publish_time；url 透传
    assert report["news"]["items"][0]["title"] == "英伟达 Spectrum-X 进入全面量产阶段，光模块景气延续"
    assert report["news"]["items"][0]["datetime"] == "2026-09-04"
    assert report["news"]["items"][0]["url"] == "https://t.wind.com.cn/mobwftweb/M/news.html?code=ABC123"
    # wind_query 必须带 api_name=get_financial_news + params.query
    wq = [c for c in tools.calls if c[0] == "wind_query"]
    assert wq and wq[0][1]["api_name"] == "get_financial_news"
    assert "query" in wq[0][1]["params"]
    # provenance：命中的是第③层
    assert report["news"]["src"] == "wind"
    assert report["news"]["src_tool"] == "wind_query"
    assert report["news"]["src_label"] == "万得 Wind"
    # 前面两层都被尝试过且失败（attempts 按序留痕）
    tried = [(a["source"], a["ok"]) for a in report["news"]["attempts"]]
    assert ("free", False) in tried
    # Tushare 那一层是第④层，Wind 命中后**不该**被打到 —— 权限错误的覆盖挪到了
    # test_news_tushare_tier_records_permission_error（这里断言它没被调用才是对的）
    assert not [c for c in tools.calls if c[0] == "news"]

    # 无 wind（wind_query 未知工具）→ na
    handlers2 = dict(handlers)
    del handlers2["wind_query"]
    report2 = await build_report(FakeTools(handlers2), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report2["news"]["status"] == "na"
    assert "news" in report2["missing"]
    assert report2["news"]["note"] == "新闻接口未接入"


async def test_one_stage_failure_does_not_affect_others(tmp_path) -> None:
    """单接口抛异常 → errors + 该段降级；其它段照常 ok。"""
    handlers = _happy_handlers()
    handlers["stk_holdertrade"] = lambda n, a: (_ for _ in ()).throw(RuntimeError("boom"))
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert any(e["stage"] == "announce" for e in report["errors"])
    assert report["board"]["status"] == "ok"
    assert report["watchlist"]["status"] == "ok"
    assert report["forecast"]["status"] == "ok"


async def test_stage_timeout_marks_na(tmp_path) -> None:
    """段级超时：handler 睡过头 → 该段 na + 「阶段超时」error；不拖垮整轮。"""
    handlers = _happy_handlers()
    handlers["stk_holdertrade"] = lambda n, a: _sleep_then_ok()

    async def _sleep_then_ok():
        await asyncio.sleep(5)
        return ToolResult("ok", is_error=False)

    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904",
                                stage_timeout=0.1, concurrency=4)
    assert report["announce"]["status"] == "na"
    assert any("超时" in e["reason"] for e in report["errors"] if e["stage"] == "announce")


async def test_all_empty_statuses(tmp_path) -> None:
    """全空（交易日无数据/接口返回空）→ 各段 empty；无 missing（链是通的）。"""
    handlers = {
        "trade_cal": lambda n, a: ToolResult(_ok([{"cal_date": "20260904", "is_open": "1"}]), is_error=False),
        "index_basic": lambda n, a: ToolResult(_ok([{"ts_code": "000001.SH", "name": "上证指数"}]), is_error=False),
        "index_daily": lambda n, a: ToolResult(_ok([]), is_error=False),  # 行情接口可达但当日无数据 → empty
        "daily": lambda n, a: ToolResult(_ok([]), is_error=False),
        "daily_basic": lambda n, a: ToolResult(_ok([]), is_error=False),
        "stk_holdertrade": lambda n, a: ToolResult(_ok([]), is_error=False),
        "repurchase": lambda n, a: ToolResult(_ok([]), is_error=False),
        "block_trade": lambda n, a: ToolResult(_ok([]), is_error=False),
        "forecast": lambda n, a: ToolResult(_ok([]), is_error=False),
        "express": lambda n, a: ToolResult(_ok([]), is_error=False),
        # 四级链每一层都「可达但本期无数据」→ news 应为 empty 而非 na
        "get_market_headlines": lambda n, a: ToolResult(_bare([]), is_error=False),
        "news": lambda n, a: ToolResult(_ok([]), is_error=False),
        "moneyflow": lambda n, a: ToolResult(_ok([]), is_error=False),
    }
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=10.0)
    assert report["board"]["status"] == "empty"
    assert report["watchlist"]["status"] == "empty"
    assert report["announce"]["status"] == "empty"
    assert report["forecast"]["status"] == "empty"
    assert report["news"]["status"] == "empty"
    assert report["missing"] == []
    assert report["brief"]["text"] == "数据不足，无法研判。"


async def test_resolve_report_date_trade_cal_and_fallback(tmp_path) -> None:
    """trade_cal 解 T-1；失败兜底昨历日。"""
    from datetime import datetime, timedelta

    from demomcp.quickreport.config import cn_tz

    # 夹具日期相对真实今天计算——硬编码具体日历日会在日期漂过后挂掉（曾因 today 越过 20260905 而失败）
    today = datetime.now(cn_tz()).date()
    d1 = (today - timedelta(days=1)).strftime("%Y%m%d")
    d2 = (today - timedelta(days=2)).strftime("%Y%m%d")
    tools = FakeTools({"trade_cal": lambda n, a: ToolResult(
        _ok([{"cal_date": d2, "is_open": "1"}, {"cal_date": d1, "is_open": "1"}]), is_error=False
    )})
    from demomcp.quickreport.pipeline import _Ctx
    ctx = _Ctx(asyncio.Semaphore(1), 10.0)
    assert await resolve_report_date(tools, ctx, None) == d1  # 今天之前最近的开市日（T-1）

    tools2 = FakeTools({"trade_cal": lambda n, a: ToolResult("Unknown tool", is_error=True)})
    ctx2 = _Ctx(asyncio.Semaphore(1), 10.0)
    yesterday = (datetime.now(cn_tz()).date() - timedelta(days=1)).strftime("%Y%m%d")
    assert await resolve_report_date(tools2, ctx2, None) == yesterday


def test_biz_fail_reason_classification() -> None:
    assert _biz_fail_reason('{"code": 40203, "msg": "积分不足，请提升积分"}') == "权限受限：积分不足，请提升积分"
    assert _biz_fail_reason('{"code": 3400, "msg": "参数错误"}') == "业务失败：参数错误"
    assert _biz_fail_reason('{"code": 0, "msg": "ok"}') is None
    assert _biz_fail_reason('{"code": "0", "msg": "ok"}') is None
    assert _biz_fail_reason('{"code": 1, "msg": ""}') == "业务失败：code 非 0"
    assert _biz_fail_reason("not json") is None
    assert _biz_fail_reason("[]") is None
    # 非 JSON 失败特征文本（mcp.py 权限友好提示 / Error calling 兜底）——否则被当成「返回空」
    assert "权限受限" in _biz_fail_reason("调用 news 失败：该接口需更高积分或当前账号无权限（代理提示：…）")
    assert "权限受限" in _biz_fail_reason("Error calling report after 2 tries: boom")
    assert "权限受限" in _biz_fail_reason("抱歉，您没有接口(sw_daily)的权限，权限的具体请求…")
    # 正常数据文本（含"失败"字样但非失败信封）不受影响
    assert _biz_fail_reason('{"code": 0, "data": [{"content": "某某公司失败案例"}]}') is None


def test_dedup_latest_forecast_drops_stale_and_dedups() -> None:
    """陈年记录（超窗口）整体丢弃；同 ts_code 多条只保留披露日最新的一条。"""
    day = "20260905"
    rows = [
        # 陈年：一年多前披露，窗口外，丢弃
        {"ts_code": "300001.SZ", "ann_date": "20241228", "period": "20241231", "p_change_min": -55.0},
        # 同一只票的新记录（窗口内），应替换掉旧的一条
        {"ts_code": "300002.SZ", "ann_date": "20260701", "period": "20260630", "p_change_min": 60.0},
        {"ts_code": "300002.SZ", "ann_date": "20260810", "period": "20260930", "p_change_min": 70.0},
        # 无 ann_date（如 ann_date=day 快照路径可能不回显该字段）→ 不因缺字段被误杀
        {"ts_code": "300003.SZ", "period": "20260930", "p_change_min": 80.0},
    ]
    out = _dedup_latest_forecast(rows, day)
    codes = {r["ts_code"]: r for r in out}
    assert "300001.SZ" not in codes
    assert codes["300002.SZ"]["ann_date"] == "20260810"
    assert "300003.SZ" in codes
    assert len(out) == 2


def test_pool_inflow_sum(tmp_path) -> None:
    """标的池整体资金流合计：池内命中行求和（亿元），未知代码/取不到值不计入。"""
    cfg = _cfg(tmp_path)
    names = {s.ts_code: s.name for s in cfg.watchlist}
    content = _ok([
        {"ts_code": "300502.SZ", "net_mf_amount": 32000},  # 3.2 亿
        {"ts_code": "300394.SZ", "net_mf_amount": -5000},  # -0.5 亿
        {"ts_code": "999999.SZ", "net_mf_amount": 100000},  # 不在池内，忽略
    ])
    assert _pool_inflow_sum(content, names)["value"] == 2.7
    assert _pool_inflow_sum(None, names)["value"] is None
    assert _pool_inflow_sum(_ok([]), names)["value"] is None


async def test_forecast_batch_gate_is_pool_filtered_not_market(tmp_path) -> None:
    """2026-09-07 审查回归：批拉兜底的门控是「池过滤后为空」——全市场快照含非池披露时也必须
    批拉，否则池内标的在窗口内的披露永远漏报（原先按全市场是否为空门控，披露季无法触发批拉）。"""
    handlers = _happy_handlers()

    def forecast_fn(n, a):
        if "ann_date" in a:
            # 当日全市场快照：只有非池公司（门控修复前 = 直接跳过批拉 → 池内标的漏报）
            return ToolResult(
                _ok([{"ts_code": "000001.SZ", "period": "20260930", "p_change_min": 80.0, "p_change_max": 90.0}]),
                is_error=False,
            )
        codes = [c for c in str(a.get("ts_code", "")).split(",") if c]
        rows = [{"ts_code": c, "period": "20260930", "p_change_min": 60.0, "p_change_max": 110.0} for c in codes]
        return ToolResult(_ok(rows), is_error=False)

    handlers["forecast"] = forecast_fn
    handlers["express"] = lambda n, a: ToolResult(_ok([]), is_error=False)
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["forecast"]["status"] == "ok"
    assert {i["name"] for i in report["forecast"]["items"]} == {"新易盛", "天孚通信"}


async def test_watchlist_daily_failure_is_na_not_empty(tmp_path) -> None:
    """2026-09-07 审查回归：主源 daily 取数失败（业务失败 → None）但 daily_basic 成功时，
    行情段必须 na（价格链断了）而不是被 basic 掩盖成 empty（『本期无标的池行情数据』）。"""
    handlers = _happy_handlers()
    handlers["daily"] = lambda n, a: ToolResult(
        '{"code": 40203, "msg": "积分不足，请提升积分"}', is_error=False
    )
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["watchlist"]["status"] == "na"
    assert "watchlist" in report["missing"]


def test_pool_inflow_sum_is_auditable_and_flags_incomplete(tmp_path) -> None:
    """池合计可审计：count/dropped/incomplete。

    刻意没有「合计超 N 亿即可疑」的判定——实测 211 只标的的池子在普涨日合计 262 亿完全正常，
    任何这类阈值都会在正常日误报。`incomplete` 只陈述事实：确实有行被守卫丢掉、合计因此偏低。
    """
    cfg = _cfg(tmp_path)
    names = {s.ts_code: s.name for s in cfg.watchlist}
    clean = _ok([
        {"ts_code": "300502.SZ", "net_mf_amount": 32000},
        {"ts_code": "300394.SZ", "net_mf_amount": -5000},
    ])
    out = _pool_inflow_sum(clean, names)
    assert out["value"] == 2.7
    assert (out["count"], out["dropped"], out["incomplete"]) == (2, 0, False)

    # 单位错乱行（远超 100 亿上界）被丢弃 → 合计偏低，必须标 incomplete
    dirty = _ok([
        {"ts_code": "300502.SZ", "net_mf_amount": 32000},
        {"ts_code": "300394.SZ", "net_mf_amount": 99999999999},
    ])
    out = _pool_inflow_sum(dirty, names)
    assert out["value"] == 3.2
    assert (out["count"], out["dropped"], out["incomplete"]) == (1, 1, True)


def test_pool_inflow_sum_keeps_real_large_single_stock_inflow(tmp_path) -> None:
    """65.48 亿（中际旭创 2026-09-07 实测）必须计入——它不是脏数据。

    此前把逐条上界收紧到 50 亿时，这条真实的池内第一被当单位错乱丢掉，
    池合计从 328 亿掉到 262 亿。「A 股没有单票单日破 50 亿」这个假设被实测推翻。
    """
    cfg = _cfg(tmp_path)
    names = {s.ts_code: s.name for s in cfg.watchlist}
    out = _pool_inflow_sum(_ok([{"ts_code": "300502.SZ", "net_mf_amount": 654819}]), names)
    assert out["value"] == 65.4819
    assert out["dropped"] == 0
    assert out["incomplete"] is False


# ---------------------------------------------------------------------------
# Phase 2：news 四级降级链（顺序与解包器均由 2026-09-08 实测确定）
# ---------------------------------------------------------------------------


async def test_news_chain_prefers_free_source(tmp_path) -> None:
    """第①层（免费源）命中就短路：iFind/Wind/Tushare 一次都不该被调用。

    改造前的顺序是 Tushare → Wind：每轮先烧两次必失败的 40203 调用，再撞上 Wind 日配额。
    """
    handlers = _happy_handlers()
    handlers["ifind_query"] = lambda n, a: ToolResult("{}", is_error=False)
    handlers["wind_query"] = lambda n, a: ToolResult("{}", is_error=False)
    tools = FakeTools(handlers)
    report = await build_report(tools, _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)

    assert report["news"]["status"] == "ok"
    assert report["news"]["src"] == "free"
    assert report["news"]["src_tool"] == "get_market_headlines"
    # `标题` 这个列名 tracker_render._NEWS 认不出（别名须是 key 的子串，`新闻标题` 不是 `标题` 的子串）
    assert report["news"]["items"][0]["title"] == "英伟达宣布量产"
    assert report["news"]["items"][0]["datetime"] == "2026-09-04 09:00:00"
    assert report["news"]["items"][0]["url"] == "https://finance.eastmoney.com/a/1.html"
    # 后面三层一次都没打
    called = {c[0] for c in tools.calls}
    assert "ifind_query" not in called
    assert "wind_query" not in called
    assert "news" not in called


async def test_news_free_error_envelope_falls_through_to_next_tier(tmp_path) -> None:
    """免费源返回 {"error": …} 必须被判失败并换层——不能当成「本期无数据」。

    这是 Phase 0 那个 GAP 的端到端锁：该形状既没有 code 字段（旧判据判成功），
    又会被 _extract_rows 当成一行数据（信封探测不认 error 键）。
    """
    handlers = _happy_handlers()
    handlers["get_market_headlines"] = lambda n, a: ToolResult(
        _free_err("get_market_headlines"), is_error=False
    )
    handlers["wind_query"] = lambda n, a: ToolResult(
        json.dumps({"data": {"items": [{"title": "Wind 兜底命中", "date": "2026-09-04"}]}}, ensure_ascii=False),
        is_error=False,
    )
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["news"]["status"] == "ok"  # 不是 empty
    assert report["news"]["src"] == "wind"
    assert any(
        e["tool"] == "get_market_headlines" and "取数失败" in e["reason"] for e in report["errors"]
    )


async def test_news_ifind_unpacks_double_encoded_data(tmp_path) -> None:
    """iFind 的 data 是**双重编码**：外层 data 里还有一层 data 是 JSON 数组字符串。

    实测列名 资讯标题 / 资讯内容 / 日期 / URL；`_extract_rows` 对它只解出 1 行
    （唯一键是尚未解码的 data 字符串）→ 必须专用解包。
    同时这条也锁住 code:1 被判成功（Phase 0）——否则整层拿不到内容。
    """
    inner = json.dumps(
        [{"资讯标题": "CPO 板块拉升，算力驱动行业高景气",
          "资讯内容": "9月7日，A股CPO板块震荡走高……",
          "日期": "2026-09-07",
          "URL": "https://example.com/ifind/1"}],
        ensure_ascii=False,
    )
    handlers = _happy_handlers()
    del handlers["get_market_headlines"]  # ① 缺席 → 落到 ②
    handlers["ifind_query"] = lambda n, a: ToolResult(
        json.dumps({"code": 1, "msg": "success", "subCode": None, "data": {"data": inner}}, ensure_ascii=False),
        is_error=False,
    )
    tools = FakeTools(handlers)
    report = await build_report(tools, _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)

    assert report["news"]["status"] == "ok"
    assert report["news"]["src"] == "ifind"
    assert report["news"]["src_label"] == "同花顺 iFind"
    item = report["news"]["items"][0]
    assert item["title"] == "CPO 板块拉升，算力驱动行业高景气"
    assert item["datetime"] == "2026-09-07"
    assert item["url"] == "https://example.com/ifind/1"
    # 只用一次 iFind 调用（套餐并发硬限 2，不能滥用）
    assert len([c for c in tools.calls if c[0] == "ifind_query"]) == 1
    iq = next(c for c in tools.calls if c[0] == "ifind_query")[1]
    assert iq["api_name"] == "search_news"
    assert "query" in iq["params"] and "time_start" in iq["params"]


async def test_news_ifind_answer_only_payload_degrades_to_next_tier(tmp_path) -> None:
    """iFind 只给 data.answer（markdown / 「未返回有效结果」）→ 返回 [] 并降级，不解析 markdown。"""
    handlers = _happy_handlers()
    del handlers["get_market_headlines"]
    handlers["ifind_query"] = lambda n, a: ToolResult(
        json.dumps({"code": 1, "msg": "success", "data": {"answer": "| 标题 | 日期 |\n|---|---|\n"}},
                   ensure_ascii=False),
        is_error=False,
    )
    handlers["wind_query"] = lambda n, a: ToolResult(
        json.dumps({"data": {"items": [{"title": "Wind 命中", "date": "2026-09-04"}]}}, ensure_ascii=False),
        is_error=False,
    )
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["news"]["status"] == "ok"
    assert report["news"]["src"] == "wind"


async def test_news_tushare_tier_records_permission_error(tmp_path) -> None:
    """第④层 Tushare：前三层全缺席时才到达，40203 → na + 权限受限留痕。

    这条承接了原 test_news_permission_then_wind_then_na 里的权限断言——
    顺序改造后 Wind 是第③层，Wind 成功时第④层永不到达。
    """
    handlers = _happy_handlers()
    del handlers["get_market_headlines"]  # ① 缺席
    # ②③ 未知工具（FakeTools 对未注册名返回 is_error=True）
    handlers["news"] = lambda n, a: ToolResult(
        '{"code": 40203, "msg": "抱歉，您没有接口(news)访问权限，请提升积分"}', is_error=False
    )
    report = await build_report(FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["news"]["status"] == "na"
    assert report["news"]["note"] == "新闻接口未接入"
    assert "news" in report["missing"]
    assert any("权限" in e["reason"] for e in report["errors"])


async def test_news_keyword_filter_and_fallback_note(tmp_path) -> None:
    """全市场头条必须按产业链关键词过滤；一条都不命中 → 退回最新若干条 + note 说明。

    实测 get_market_headlines 同一批返回里混着「《全国渔业发展十五五规划》印发」
    这种与算力无关的条目——不过滤就会出现在算力快报里。
    """
    rows = [
        {"标题": "《全国渔业发展十五五规划》印发", "摘要": "农业农村部", "发布时间": "2026-09-04 10:00:00", "链接": "u1"},
        {"标题": "华丰科技：224G 高速背板连接器批量交付", "摘要": "算力相关", "发布时间": "2026-09-04 09:30:00", "链接": "u2"},
    ]
    handlers = _happy_handlers()
    handlers["get_market_headlines"] = lambda n, a: ToolResult(_bare(rows), is_error=False)

    # 命中：只保留相关条目
    cfg_hit = _cfg(tmp_path, news_keywords=["光模块", "算力", "连接器"])
    rep = await build_report(FakeTools(handlers), cfg_hit, report_date="20260904", stage_timeout=20.0)
    titles = [i["title"] for i in rep["news"]["items"]]
    assert titles == ["华丰科技：224G 高速背板连接器批量交付"]
    assert rep["news"]["note"] is None

    # 全不命中：退回最新条目，但必须写 note（不能假装「本期无催化」）
    cfg_miss = _cfg(tmp_path, news_keywords=["核聚变"])
    rep2 = await build_report(FakeTools(handlers), cfg_miss, report_date="20260904", stage_timeout=20.0)
    assert rep2["news"]["status"] == "ok"
    assert len(rep2["news"]["items"]) == 2
    assert rep2["news"]["note"] == "无产业链关键词命中，展示市场头条"


async def test_news_stock_codes_merge_and_dedup(tmp_path) -> None:
    """可选的个股新闻并入并按标题去重；6 位裸代码（免费源不吃 .SZ/.SH 后缀）。"""
    handlers = _happy_handlers()
    handlers["get_stock_news"] = lambda n, a: ToolResult(
        _bare([
            {"新闻标题": "英伟达宣布量产", "发布时间": "2026-09-04 08:00:00",
             "文章来源": "证券时报", "新闻链接": "u9"},  # 与头条重复 → 去重
            {"新闻标题": "中际旭创回购超 3 亿元", "发布时间": "2026-09-04 07:00:00",
             "文章来源": "证券时报", "新闻链接": "u10"},
        ]),
        is_error=False,
    )
    cfg = _cfg(tmp_path, news_stock_codes=["300308"])
    tools = FakeTools(handlers)
    rep = await build_report(tools, cfg, report_date="20260904", stage_timeout=20.0)
    titles = [i["title"] for i in rep["news"]["items"]]
    assert titles.count("英伟达宣布量产") == 1  # 去重生效
    assert "中际旭创回购超 3 亿元" in titles
    gs = [c for c in tools.calls if c[0] == "get_stock_news"]
    assert gs and gs[0][1]["ticker"] == "300308"  # 裸码，不带后缀


async def test_news_section_carries_provenance(tmp_path) -> None:
    """每段都带 src/src_tool/src_label/attempts/fetched_at（随报文归档，供前端来源看板）。"""
    report = await build_report(
        FakeTools(_happy_handlers()), _cfg(tmp_path), report_date="20260904", stage_timeout=20.0
    )
    for name in ("board", "watchlist", "announce", "forecast", "news"):
        sec = report[name]
        assert set(sec) >= {"status", "note", "src", "src_tool", "src_label", "attempts", "fetched_at"}, name
        assert sec["fetched_at"].endswith("+08:00"), name  # 必须带偏移，否则前端会当 UTC 早显示 8 小时
    assert report["board"]["src_tool"] == "index_daily"
    assert report["watchlist"]["src_tool"] == "daily"
    assert report["news"]["src"] == "free"
    # errors 每条都带 source（段级超时那种伪工具名不能被误判成 Tushare 接口）
    for e in report["errors"]:
        assert "source" in e


async def test_stage_timeout_returns_three_tuple_for_news(tmp_path) -> None:
    """news 段改成三元返回后，段级超时的 fallback 也必须是三元——否则解包处直接 ValueError。"""
    import asyncio as _asyncio

    async def _hang(name, args):
        await _asyncio.sleep(5)
        return ToolResult(_bare([]), is_error=False)

    handlers = _happy_handlers()
    handlers["get_market_headlines"] = _hang
    report = await build_report(
        FakeTools(handlers), _cfg(tmp_path), report_date="20260904", stage_timeout=0.1
    )
    assert report["news"]["status"] == "na"
    assert isinstance(report["news"]["items"], list)
    assert any("超时" in e["reason"] for e in report["errors"])


# ---------------------------------------------------------------------------
# Phase 3：指数走势序列（board.series 子键，不是第七段）
# ---------------------------------------------------------------------------


async def test_board_series_absent_when_unconfigured(tmp_path) -> None:
    """默认 board.series = () → **一次调用都不产生**，errors 保持干净。

    这是「做成子键 + 默认空」而不是第七段的关键收益：现有 fixture 完全不受影响
    （六段全通那个测试断言 errors == []，新段缺 handler 会立刻打破它）。
    """
    tools = FakeTools(_happy_handlers())
    report = await build_report(tools, _cfg(tmp_path), report_date="20260904", stage_timeout=20.0)
    assert report["board"]["series"] == []
    assert report["board"]["series_status"] == "na"
    assert report["errors"] == []
    assert not [c for c in tools.calls if c[0] == "get_index_data"]
    # 子键失败不进顶层 missing（missing 只看五段的 status）
    assert report["missing"] == []


async def test_board_series_shape_and_order(tmp_path) -> None:
    """Tushare index_daily 实测是**裸数组且降序**——必须重排成升序并取尾部 days。"""
    # 降序（最新在前），模拟真实返回；6 天数据配 series_days=5 → 应丢掉最早那天
    rows = [
        {"ts_code": "000001.SH", "trade_date": "20260904", "open": 10.0, "close": 12.0,
         "low": 9.5, "high": 12.5, "vol": 300},
        {"ts_code": "000001.SH", "trade_date": "20260903", "open": 9.0, "close": 10.0,
         "low": 8.8, "high": 10.2, "vol": 200},
        {"ts_code": "000001.SH", "trade_date": "20260902", "open": 8.0, "close": 9.0,
         "low": 7.9, "high": 9.1, "vol": 100},
        {"ts_code": "000001.SH", "trade_date": "20260901", "open": 7.0, "close": 8.0,
         "low": 6.9, "high": 8.1, "vol": 90},
        {"ts_code": "000001.SH", "trade_date": "20260831", "open": 6.0, "close": 7.0,
         "low": 5.9, "high": 7.1, "vol": 80},
        {"ts_code": "000001.SH", "trade_date": "20260828", "open": 5.0, "close": 6.0,
         "low": 4.9, "high": 6.1, "vol": 70},
    ]
    handlers = _happy_handlers()
    handlers["index_daily"] = lambda n, a: ToolResult(_bare(rows), is_error=False)
    # series_days 下限被 config 夹到 5（2 个点画不出走势），这里直接用 5
    cfg = _cfg(tmp_path, board={
        "indexes": ["上证指数"], "dc_flow": False,
        "series": [{"name": "上证指数", "code": "000001.SH"}], "series_days": 5,
    })
    report = await build_report(FakeTools(handlers), cfg, report_date="20260904", stage_timeout=20.0)

    assert report["board"]["series_status"] == "ok"
    s = report["board"]["series"][0]
    assert s["name"] == "上证指数" and s["code"] == "000001.SH"
    # ECharts 蜡烛图的 value 顺序：open, close, low, high
    assert s["fields"] == ["date", "open", "close", "low", "high", "volume"]
    assert s["count"] == 5  # series_days=5 → 取尾部 5 天，最早的 08-28 被丢
    assert [r[0] for r in s["rows"]] == [
        "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
    ]  # 升序（源是降序）
    assert s["rows"][-1] == ["2026-09-04", 10.0, 12.0, 9.5, 12.5, 300.0]
    # last.pct 由最后两个收盘推出（12.0 vs 10.0 → +20%），不额外调接口
    assert s["last"]["date"] == "2026-09-04"
    assert s["last"]["close"] == 12.0
    assert s["last"]["pct"] == 20.0
    assert s["last"]["pct_text"] == "+20.00%"


async def test_board_series_falls_back_to_akshare(tmp_path) -> None:
    """Tushare 失败 → AkShare get_index_data 兜底，且必须传 **6 位裸码**（不带 .SH/.SZ）。"""
    ak_rows = [  # AkShare 是升序、列名 date/open/close/low/high/volume
        {"date": "2026-09-03", "open": 9.0, "close": 10.0, "low": 8.8, "high": 10.2, "volume": 200},
        {"date": "2026-09-04", "open": 10.0, "close": 11.0, "low": 9.9, "high": 11.2, "volume": 300},
    ]
    handlers = _happy_handlers()
    handlers["index_daily"] = lambda n, a: ToolResult("ERR", is_error=True)
    handlers["get_index_data"] = lambda n, a: ToolResult(_bare(ak_rows), is_error=False)
    cfg = _cfg(tmp_path, board={
        "indexes": [], "dc_flow": False,
        "series": [{"name": "沪深300", "code": "000300.SH"}], "series_days": 120,
    })
    tools = FakeTools(handlers)
    report = await build_report(tools, cfg, report_date="20260904", stage_timeout=20.0)

    s = report["board"]["series"][0]
    assert s["count"] == 2
    assert s["last"]["close"] == 11.0
    gi = next(c for c in tools.calls if c[0] == "get_index_data")[1]
    assert gi["index_code"] == "000300"  # 裸码


async def test_board_series_failure_does_not_change_board_status(tmp_path) -> None:
    """序列全败时 board 段自身仍是 ok —— 两件事的严重性不同，不能互相污染。"""
    handlers = _happy_handlers()
    handlers["index_daily"] = lambda n, a: ToolResult(
        _ok([{"ts_code": a.get("ts_code"), "trade_date": "20260904", "close": 4000.0, "pct_chg": 0.85}])
        if a.get("trade_date") or a.get("start_date") == "20260904" else _free_err("index_daily"),
        is_error=False,
    )
    handlers["get_index_data"] = lambda n, a: ToolResult(_free_err("get_index_data"), is_error=False)
    cfg = _cfg(tmp_path, board={
        "indexes": ["上证指数"], "dc_flow": False,
        "series": [{"name": "上证指数", "code": "000001.SH"}], "series_days": 120,
    })
    report = await build_report(FakeTools(handlers), cfg, report_date="20260904", stage_timeout=20.0)
    assert report["board"]["status"] == "ok"       # 板块段照常
    assert report["board"]["series_status"] == "na"  # 只有子键 na
    assert "board" not in report["missing"]


def test_build_index_series_drops_bad_rows_and_dedups() -> None:
    """无日期/无收盘的行丢弃（x 轴对不齐比少个点更糟）；同日重复保后者；空序列 last 为 {}。"""
    from demomcp.quickreport.projection import build_index_series

    rows = [
        {"trade_date": "20260902", "close": 9.0},
        {"trade_date": "", "close": 8.0},          # 无日期 → 丢
        {"trade_date": "20260903", "close": None},  # 无收盘 → 丢
        {"trade_date": "20260902", "close": 9.5},   # 同日重复 → 保后者
        {"trade_date": "20260904", "close": 10.0},
    ]
    s = build_index_series("X", "000001.SH", rows, days=120)
    assert [r[0] for r in s["rows"]] == ["2026-09-02", "2026-09-04"]
    assert s["rows"][0][2] == 9.5  # 后者胜
    assert s["count"] == 2

    empty = build_index_series("X", "000001.SH", [], days=120)
    assert empty["rows"] == [] and empty["count"] == 0 and empty["last"] == {}


def test_build_index_series_normalizes_both_date_formats() -> None:
    """Tushare 给 `20260907`、AkShare 给 `2026-09-07` —— 统一成 ISO。"""
    from demomcp.quickreport.projection import build_index_series

    a = build_index_series("A", "c", [{"trade_date": "20260907", "close": 1.0}])
    b = build_index_series("B", "c", [{"date": "2026-09-07", "close": 1.0}])
    assert a["rows"][0][0] == b["rows"][0][0] == "2026-09-07"
