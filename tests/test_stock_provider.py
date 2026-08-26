"""语义工具层 · StockToolProvider 离线测试（用假底层 provider 桩住每接口返回，验证 allowlist / 语义工具 / 错误约定）。"""

from __future__ import annotations

import json
from typing import Any

from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.stocks import StockToolProvider


class _StubUnderlying:
    """桩底层 ToolProvider：按工具名返回 {code,msg,row_count,data} JSON；未注册名字返回 Unknown tool。"""

    def __init__(self, data: dict[str, dict[str, Any]] | None = None) -> None:
        self._data = data or {}
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def list_tools(self) -> list[ToolSpec]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        self.calls.append((name, arguments))
        spec = self._data.get(name)
        if spec is None:
            return ToolResult(content=f"Unknown tool: {name}", is_error=True)
        rows = spec.get("rows", [])
        code = spec.get("code", 0)
        return ToolResult(
            content=json.dumps(
                {"code": code, "msg": spec.get("msg", "ok"), "row_count": len(rows), "data": rows},
                ensure_ascii=False,
            ),
            is_error=bool(spec.get("is_error", False)),
        )


def _data(payload: str) -> dict[str, Any]:
    return json.loads(payload)


async def test_list_tools_exposes_only_semantic() -> None:
    provider = StockToolProvider(_StubUnderlying())
    names = [s.name for s in await provider.list_tools()]
    assert names == ["stock_available", "stock_realtime_quote", "stock_price_range", "stock_financials"]


async def test_call_unknown_tool_iserror() -> None:
    provider = StockToolProvider(_StubUnderlying())
    result = await provider.call_tool("nope")
    assert result.is_error is True
    assert "Unknown tool" in result.content


async def test_available_returns_two() -> None:
    provider = StockToolProvider(_StubUnderlying())
    result = await provider.call_tool("stock_available")
    assert result.is_error is False
    body = _data(result.content)
    assert body["tool"] == "stock_available"
    codes = {c["ts_code"] for c in body["data"]["companies"]}
    assert codes == {"002594.SZ", "300750.SZ"}


async def test_allowlist_reject_friendly() -> None:
    provider = StockToolProvider(_StubUnderlying())
    result = await provider.call_tool("stock_realtime_quote", {"stock": "600519"})
    assert result.is_error is False  # 校验类错误 → 友好，不崩
    assert "仅支持" in result.content


async def test_realtime_from_realtime_quote() -> None:
    stub = _StubUnderlying({
        "realtime_quote": {"rows": [
            {"ts_code": "002594.SZ", "name": "比亚迪", "price": 260.0, "pct_change": 1.5,
             "pe_ttm": 22.0, "pb": 4.0, "total_mv": 8000000.0, "vol": 9000, "amount": 2.4e7},
        ]},
    })
    result = await StockToolProvider(stub).call_tool("stock_realtime_quote", {"stock": "比亚迪"})
    assert result.is_error is False
    body = _data(result.content)
    assert body["company"] == "比亚迪"
    assert body["ts_code"] == "002594.SZ"
    assert body["data"]["price"] == 260.0
    assert body["data"]["source"] == "realtime_quote"
    assert "realtime_quote" in stub.calls[0][0] or stub.calls[0][0] == "realtime_quote"


async def test_realtime_falls_back_to_eod() -> None:
    stub = _StubUnderlying({
        "realtime_quote": {"rows": []},  # 实时无数据
        "daily": {"rows": [
            {"ts_code": "002594.SZ", "trade_date": "20260824", "close": 258.0, "pct_change": 0.4, "vol": 8000, "amount": 2e7},
        ]},
        "daily_basic": {"rows": [
            {"ts_code": "002594.SZ", "trade_date": "20260824", "pe_ttm": 22.5, "pb": 3.9, "total_mv": 7900000.0},
        ]},
    })
    result = await StockToolProvider(stub).call_tool("stock_realtime_quote", {"stock": "比亚迪"})
    assert result.is_error is False
    body = _data(result.content)
    assert body["data"]["source"] == "daily+daily_basic(最新EOD回落)"
    assert body["data"]["price"] == 258.0
    assert "daily_basic" in {c for c, _ in stub.calls}


async def test_realtime_fallback_handles_business_error() -> None:
    stub = _StubUnderlying({
        "realtime_quote": {"rows": [], "code": 40001, "msg": "该接口需更高积分"},  # 业务失败，需回落
        "daily": {"rows": [
            {"ts_code": "002594.SZ", "trade_date": "20260824", "close": 258.0, "pct_change": 0.4, "vol": 8000, "amount": 2e7},
        ]},
        "daily_basic": {"rows": []},
    })
    result = await StockToolProvider(stub).call_tool("stock_realtime_quote", {"stock": "002594"})
    assert result.is_error is False
    assert _data(result.content)["data"]["source"].startswith("daily+daily_basic")


async def test_price_range_computes_qfq_return() -> None:
    stub = _StubUnderlying({
        "adj_factor": {"rows": [
            {"trade_date": "20260101", "adj_factor": 1.0},
            {"trade_date": "20260102", "adj_factor": 1.0},
        ]},
        "daily": {"rows": [
            {"trade_date": "20260101", "open": 100, "high": 110, "low": 95, "close": 100, "vol": 1000, "amount": 1e5},
            {"trade_date": "20260102", "open": 100, "high": 120, "low": 90, "close": 120, "vol": 2000, "amount": 2e5},
        ]},
        "daily_basic": {"rows": [
            {"trade_date": "20260101", "pe_ttm": 20.0, "pb": 3.0},
            {"trade_date": "20260102", "pe_ttm": 21.0, "pb": 3.1},
        ]},
    })
    result = await StockToolProvider(stub).call_tool("stock_price_range", {"stock": "宁德时代", "start": "2026-01-01", "end": "2026-02-01"})
    assert result.is_error is False
    body = _data(result.content)
    assert body["data"]["adj"] == "qfq"
    assert body["data"]["return_pct"] == 20.0  # (120/100 - 1) * 100
    assert body["data"]["sample_days"] == 2
    assert body["data"]["pe_ttm_end"] == 21.0
    # 锁定官方 MCP 扁平入参 + fields 数组 契约
    daily_args = next(a for n, a in stub.calls if n == "daily")
    assert daily_args["fields"] == ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]
    assert daily_args["ts_code"] == "300750.SZ"


async def test_price_range_too_few_days() -> None:
    stub = _StubUnderlying({
        "adj_factor": {"rows": []},
        "daily": {"rows": [{"trade_date": "20260101", "close": 100, "vol": 1, "amount": 1}]},
    })
    result = await StockToolProvider(stub).call_tool("stock_price_range", {"stock": "比亚迪", "start": "2026-01-01", "end": "2026-01-02"})
    assert result.is_error is False
    assert "区间交易日不足" in result.content


async def test_financials_merges_income_and_fina() -> None:
    stub = _StubUnderlying({
        "income": {"rows": [{"end_date": "20240630", "revenue": 100.0, "total_revenue": 100.0, "n_income_attr_p": 10.0, "n_income": 10.0}]},
        "fina_indicator": {"rows": [{"end_date": "20240630", "grossprofit_margin": 20.0, "debt_to_assets": 50.0, "netprofit_margin": 10.0, "roe": 8.0, "eps": 1.0}]},
    })
    result = await StockToolProvider(stub).call_tool("stock_financials", {"stock": "宁德时代", "period": "20240630"})
    assert result.is_error is False
    body = _data(result.content)
    row = body["data"]["periods"][0]
    assert row["label"] == "2024Q2"
    assert row["revenue"] == 100.0
    assert row["net_profit_attr_p"] == 10.0
    assert row["gross_margin"] == 20.0
    assert row["debt_ratio"] == 50.0


async def test_financials_no_data_friendly() -> None:
    stub = _StubUnderlying({"income": {"rows": []}, "fina_indicator": {"rows": []}})
    result = await StockToolProvider(stub).call_tool("stock_financials", {"stock": "比亚迪", "period": "20240630"})
    assert result.is_error is False
    assert "无可用的指定期次财报数据" in result.content


async def test_adj_factor_unavailable_degrades_to_none() -> None:
    # 底层没有 adj_factor 接口工具（Unknown tool → 取数失败）→ stock_price_range 降级为不复权，不崩
    stub = _StubUnderlying({
        "daily": {"rows": [
            {"trade_date": "20260101", "close": 100, "vol": 1, "amount": 1},
            {"trade_date": "20260102", "close": 110, "vol": 1, "amount": 1},
        ]},
    })
    result = await StockToolProvider(stub).call_tool("stock_price_range", {"stock": "002594", "start": "2026-01-01", "end": "2026-01-02", "adj": "none"})
    assert result.is_error is False
    body = _data(result.content)
    assert body["data"]["adj"] == "none"
    assert body["data"]["return_pct"] == 10.0
    assert any(name == "adj_factor" for name, _ in stub.calls)
    assert not any(name == "query" for name, _ in stub.calls)  # 不再回退 query
