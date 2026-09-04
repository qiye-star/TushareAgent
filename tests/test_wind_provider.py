"""万得 WindToolProvider / agent_tool_provider 离线测试：不联网，用假 provider 与假 mcp_tool_provider 覆盖路由与鉴权。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools import wind
from demomcp.providers.tools.wind import WindToolProvider


class _FakeProvider:
    """哑 provider：list_tools 返回预设 specs；call_tool 走预设 handler（默认记录原始名并返回 ok）。"""

    def __init__(self, specs, handler=None) -> None:
        self._specs = specs
        self.called: list[tuple[str, dict | None]] = []
        self._handler = handler or (lambda n, a: ToolResult(content=f"ok:{n}", is_error=False))

    async def list_tools(self) -> list[ToolSpec]:
        return self._specs

    async def call_tool(self, name, arguments=None) -> ToolResult:
        self.called.append((name, arguments))
        return self._handler(name, arguments)


def _fake_get_provider(domain_specs_by_domain, *, failing: set[str] | None = None):
    """构造一个 WindToolProvider._get_provider 的替身：按 domain 返回（或抛出）对应假 provider。"""

    async def fake(self, domain):
        if domain in (failing or set()):
            raise RuntimeError(f"domain {domain} down")
        return domain_specs_by_domain[domain]

    return fake


async def test_list_tools_prefixes_and_indexes(monkeypatch) -> None:
    stock = _FakeProvider([ToolSpec("get_stock_quote"), ToolSpec("get_stock_kline")])
    fund = _FakeProvider([ToolSpec("get_fund_quote")])
    provs = {"stock_data": stock, "fund_data": fund}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))

    wtp = WindToolProvider("ak_x")
    specs = await wtp.list_tools()
    assert [s.name for s in specs] == [
        "wind_get_stock_quote",
        "wind_get_stock_kline",
        "wind_get_fund_quote",
    ]
    # 之后 call_tool(带前缀) 能按原始名路由到对应域
    await wtp.call_tool("wind_get_stock_kline", {"windcode": "600519.SH"})
    assert stock.called == [("get_stock_kline", {"windcode": "600519.SH"})]


async def test_list_tools_skips_down_domain(monkeypatch) -> None:
    fund = _FakeProvider([ToolSpec("get_fund_quote")])
    provs = {"stock_data": _FakeProvider([ToolSpec("get_stock_quote")]), "fund_data": fund}
    monkeypatch.setattr(
        WindToolProvider, "_get_provider", _fake_get_provider(provs, failing={"stock_data"})
    )
    specs = await WindToolProvider("ak_x").list_tools()
    assert [s.name for s in specs] == ["wind_get_fund_quote"]


async def test_call_tool_unknown_and_unprefixed_are_error(monkeypatch) -> None:
    provs = {"stock_data": _FakeProvider([ToolSpec("get_stock_quote")])}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))

    wtp = WindToolProvider("ak_x")
    await wtp.list_tools()
    # 未知工具名
    assert (await wtp.call_tool("wind_nope", {})).is_error is True
    # 不以 wind_ 开头：非本 provider 的工具
    assert (await wtp.call_tool("get_stock_quote", {})).is_error is True


async def test_call_tool_domain_failure_is_iserror(monkeypatch) -> None:
    # 列表成功后单域连接失败 → is_error，不崩
    provs = {"stock_data": _FakeProvider([ToolSpec("get_stock_quote")])}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))

    wtp = WindToolProvider("ak_x")
    await wtp.list_tools()  # index 已建：get_stock_quote → stock_data
    # 让 call_tool 阶段重新走 _get_provider，且本次抛（模拟域掉线）
    monkeypatch.setattr(
        WindToolProvider, "_get_provider", _fake_get_provider(provs, failing={"stock_data"})
    )
    wtp._providers.clear()
    result = await wtp.call_tool("wind_get_stock_quote", {})
    assert result.is_error is True


async def test_open_passes_bearer_header_and_reuses_mcp_tool_provider(monkeypatch) -> None:
    """_open 应把 WIND_API_KEY 拼成 Authorization: Bearer 头并透传给 mcp_tool_provider。"""
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_mcp_provider(url, *, timeout=30.0, retries=2, headers=None):
        captured.update(
            url=url, timeout=timeout, retries=retries, headers=headers
        )
        class _Stub:
            async def list_tools(self):
                return []

            async def call_tool(self, *a, **k):
                return ToolResult("stub", is_error=False)

        yield _Stub()

    monkeypatch.setattr(wind, "mcp_tool_provider", fake_mcp_provider)

    wtp = WindToolProvider("ak_x", timeout=7, retries=1)
    async with wtp:
        await wtp._get_provider("stock_data")

    assert captured["url"] == wind.WIND_DOMAINS["stock_data"]
    assert captured["headers"] == {"Authorization": "Bearer ak_x"}
    assert captured["timeout"] == 7
    assert captured["retries"] == 1


async def test_open_treats_missing_key_as_no_auth(monkeypatch) -> None:
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_mcp_provider(url, *, timeout=30.0, retries=2, headers=None):
        captured["headers"] = headers
        class _Stub:
            async def list_tools(self):
                return []

            async def call_tool(self, *a, **k):
                return ToolResult("stub", is_error=False)

        yield _Stub()

    monkeypatch.setattr(wind, "mcp_tool_provider", fake_mcp_provider)

    async with WindToolProvider("") as wtp:
        await wtp._get_provider("fund_data")
    assert captured["headers"] is None


def test_wind_error_signal_mapping() -> None:
    assert wind._friendly_wind_error("RATE_LIMIT_ERROR", "") == "万得限流，请稍后再试或减少并发请求。"
    assert wind._friendly_wind_error("backend_error", "") == "万得后端返回错误，请稍后重试。"
    assert wind._friendly_wind_error("AUTH_ERROR", "") == "万得鉴权失败，请检查 WIND_API_KEY。"
    assert wind._friendly_wind_error("OUT_OF_SCOPE", "") == "该查询超出万得支持范围。"
    assert wind._friendly_wind_error("PARAM_VALIDATION_ERROR", "bad windcode") == "万得参数校验失败：bad windcode"
    assert "限流" in wind._wind_error_signal('{"ok": false, "code": "RATE_LIMIT_ERROR", "message": "qps"}')
    assert "后端" in wind._wind_error_signal('{"error": {"code": "backend_error", "message": "boom"}}')
    # 成功/非错误信封 → None（原样通过）
    assert wind._wind_error_signal('{"data": {"rows": []}, "error": null}') is None
    assert wind._wind_error_signal("not json") is None
    assert wind._wind_error_signal("[]") is None


async def test_call_tool_normalizes_error_envelope(monkeypatch) -> None:
    def handler(name, args):
        return ToolResult(content='{"ok": false, "code": "RATE_LIMIT_ERROR", "message": "qps"}', is_error=False)

    provs = {"stock_data": _FakeProvider([ToolSpec("get_stock_quote")], handler)}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))
    wtp = WindToolProvider("ak_x")
    await wtp.list_tools()
    result = await wtp.call_tool("wind_get_stock_quote", {"windcode": "600519.SH"})
    assert result.is_error is True
    assert "限流" in result.content


async def test_call_tool_concurrency_capped(monkeypatch) -> None:
    in_flight = 0
    peak = 0

    class _SlowProvider:
        async def list_tools(self):
            return [ToolSpec("get_stock_quote")]

        async def call_tool(self, name, args=None):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return ToolResult(content="ok", is_error=False)

    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider({"stock_data": _SlowProvider()}))
    wtp = WindToolProvider("ak_x", max_concurrency=1)
    await wtp.list_tools()
    await asyncio.gather(
        wtp.call_tool("wind_get_stock_quote", {}), wtp.call_tool("wind_get_stock_quote", {})
    )
    assert peak == 1  # max_concurrency=1 时并发调用被串行化
