"""万得 WindToolProvider 离线测试：不联网，用假 provider 与假 mcp_tool_provider 覆盖路由与鉴权。

（原先这里还测 `agent_tool_provider`——它已随「MCP 网关成为唯一取数路径」删除：多源装配现在是
`mcp_gateway/sources.py` + `mcp_gateway/pool.py` 的职责，见 tests/test_mcp_gateway_pool.py。）
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from demomcp.interfaces.types import (
    META_TOOL_NAMES,
    WIND_GET_API_INFO,
    WIND_LIST_APIS,
    WIND_META_TOOL_NAMES,
    WIND_QUERY,
    ToolResult,
    ToolSpec,
)
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


async def test_list_tools_returns_only_meta_trio(monkeypatch) -> None:
    """list_tools() 对外只暴露 3 个懒发现元工具，不再摊开具体 wind_* schema；each schema 非空。"""
    stock = _FakeProvider([ToolSpec("get_stock_quote"), ToolSpec("get_stock_kline")])
    fund = _FakeProvider([ToolSpec("get_fund_quote")])
    provs = {"stock_data": stock, "fund_data": fund}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))

    wtp = WindToolProvider("ak_x")
    specs = await wtp.list_tools()
    assert [s.name for s in specs] == [WIND_LIST_APIS, WIND_GET_API_INFO, WIND_QUERY]
    assert all(s.input_schema for s in specs)

    # 之后 wind_query 能按具体 api_name 路由到对应域（复用 _dispatch）
    await wtp.call_tool(WIND_QUERY, {"api_name": "get_stock_kline", "params": {"windcode": "600519.SH"}})
    assert stock.called == [("get_stock_kline", {"windcode": "600519.SH"})]
    # 历史直连路径（具体名不经 wind_query）仍然保留，行为不变
    await wtp.call_tool("wind_get_stock_kline", {"windcode": "000001.SZ"})
    assert stock.called[-1] == ("get_stock_kline", {"windcode": "000001.SZ"})


async def test_list_apis_reflects_down_domain_without_new_network_call(monkeypatch) -> None:
    """wind_list_apis 反映掉线域缺席/健康域在场；命中缓存后不再触发新的域连接。"""
    calls = 0
    fund = _FakeProvider([ToolSpec("get_fund_quote")])

    async def fake(self, domain):
        nonlocal calls
        calls += 1
        if domain == "stock_data":
            raise RuntimeError("domain stock_data down")
        return fund

    monkeypatch.setattr(WindToolProvider, "_get_provider", fake)
    wtp = WindToolProvider("ak_x")
    await wtp.list_tools()
    calls_after_list = calls

    result = await wtp.call_tool(WIND_LIST_APIS, {})
    payload = json.loads(result.content)
    names = [a["name"] for a in payload["apis"]]
    assert "wind_get_fund_quote" in names
    assert not any("stock" in n for n in names)
    assert calls == calls_after_list  # 命中缓存，没有再触发任何域连接


async def test_down_domain_retries_after_cooldown(monkeypatch) -> None:
    """掉线域不会被永久打入冷宫：冷却期内不重试，冷却期过后下次调用会重新尝试连接。

    2026-09 事故：_ensure_concrete_catalog 曾是「只做一次」的永久缓存——某域在建目录那一刻
    若恰好瞬时故障（如 502 Bad Gateway），就会永久从 wind_list_apis 消失，直到 30 分钟后
    进程级工具池整体重建才恢复。用假时钟精确控制冷却窗口，不真的 sleep。
    """

    class _Clock:
        def __init__(self) -> None:
            self.t = 0.0

        def __call__(self) -> float:
            return self.t

    clock = _Clock()
    monkeypatch.setattr(wind.time, "monotonic", clock)

    stock = _FakeProvider([ToolSpec("get_stock_quote")])
    others = {d: _FakeProvider([ToolSpec(f"get_{d}")]) for d in wind.WIND_DOMAINS if d != "stock_data"}
    attempts = {"stock_data": 0}

    async def fake(self, domain):
        if domain == "stock_data":
            attempts["stock_data"] += 1
            if attempts["stock_data"] == 1:
                raise RuntimeError("domain stock_data down (transient 502)")
            return stock
        return others[domain]

    monkeypatch.setattr(WindToolProvider, "_get_provider", fake)
    wtp = WindToolProvider("ak_x")

    await wtp.list_tools()  # 第一次：stock_data 502，其余域正常
    names = [a["name"] for a in json.loads((await wtp.call_tool(WIND_LIST_APIS, {})).content)["apis"]]
    assert "wind_get_stock_quote" not in names
    assert attempts["stock_data"] == 1

    clock.t += 1.0  # 冷却期内（默认 60s）→ 仍不重试
    await wtp.list_tools()
    assert attempts["stock_data"] == 1

    clock.t += wind._DOMAIN_RETRY_COOLDOWN  # 冷却期过后 → 重试并成功
    await wtp.list_tools()
    names2 = [a["name"] for a in json.loads((await wtp.call_tool(WIND_LIST_APIS, {})).content)["apis"]]
    assert "wind_get_stock_quote" in names2
    assert attempts["stock_data"] == 2


async def test_list_apis_keyword_filters() -> None:
    wtp = WindToolProvider("ak_x")
    wtp._specs = [ToolSpec("wind_get_stock_quote", "股票行情"), ToolSpec("wind_get_fund_quote", "基金行情")]
    result = await wtp._call_list_apis({"keyword": "stock"})
    payload = json.loads(result.content)
    assert [a["name"] for a in payload["apis"]] == ["wind_get_stock_quote"]


async def test_get_api_info_known_and_unknown(monkeypatch) -> None:
    provs = {"stock_data": _FakeProvider([ToolSpec("get_stock_quote", "行情", {"type": "object"})])}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))
    wtp = WindToolProvider("ak_x")
    await wtp.list_tools()

    ok = await wtp.call_tool(WIND_GET_API_INFO, {"api_name": "get_stock_quote"})  # 裸名也接受
    payload = json.loads(ok.content)
    assert payload["name"] == "wind_get_stock_quote"
    assert payload["input_schema"] == {"type": "object"}

    bad = await wtp.call_tool(WIND_GET_API_INFO, {"api_name": "wind_nope"})
    assert bad.is_error is True


async def test_query_rejects_missing_api_name_or_bad_params() -> None:
    wtp = WindToolProvider("ak_x")
    wtp._specs = []
    missing = await wtp.call_tool(WIND_QUERY, {})
    assert missing.is_error is True
    bad_params = await wtp.call_tool(WIND_QUERY, {"api_name": "get_stock_quote", "params": "nope"})
    assert bad_params.is_error is True


async def test_query_reuses_error_normalization(monkeypatch) -> None:
    """wind_query 是 _dispatch 的薄包装：限流等信封归一化与直连路径完全一致。"""

    def handler(name, args):
        return ToolResult(content='{"ok": false, "code": "RATE_LIMIT_ERROR", "message": "qps"}', is_error=False)

    provs = {"stock_data": _FakeProvider([ToolSpec("get_stock_quote")], handler)}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))
    wtp = WindToolProvider("ak_x")
    await wtp.list_tools()
    result = await wtp.call_tool(WIND_QUERY, {"api_name": "get_stock_quote", "params": {"windcode": "600519.SH"}})
    assert result.is_error is True
    assert "限流" in result.content


def test_wind_meta_tool_names_subset_of_global_meta() -> None:
    assert WIND_META_TOOL_NAMES <= META_TOOL_NAMES


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


async def test_open_passes_keepalive_when_set(monkeypatch) -> None:
    """keepalive_interval 配置时透传给 mcp_tool_provider（未配置时不传——见上一个断言保持旧签名）。"""
    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_mcp_provider(url, *, timeout=30.0, retries=2, headers=None, keepalive_interval=None):
        captured.update(
            url=url, timeout=timeout, retries=retries, headers=headers,
            keepalive_interval=keepalive_interval,
        )
        class _Stub:
            async def list_tools(self):
                return []

            async def call_tool(self, *a, **k):
                return ToolResult("stub", is_error=False)

        yield _Stub()

    monkeypatch.setattr(wind, "mcp_tool_provider", fake_mcp_provider)

    wtp = WindToolProvider("ak_x", timeout=7, retries=1, keepalive_interval=45.0)
    async with wtp:
        await wtp._get_provider("stock_data")

    assert captured["keepalive_interval"] == 45.0


async def test_warm_up_populates_catalog(monkeypatch) -> None:
    """warm_up 应预连各域并填具体目录（_specs/_index/_by_name）。"""
    stock = _FakeProvider([ToolSpec("get_stock_quote")])
    fund = _FakeProvider([ToolSpec("get_fund_quote")])
    provs = {"stock_data": stock, "fund_data": fund}
    monkeypatch.setattr(WindToolProvider, "_get_provider", _fake_get_provider(provs))

    wtp = WindToolProvider("ak_x")
    async with wtp:
        await wtp.warm_up()

    assert wtp._specs  # 非空（_specs 恒为 list，不再用 is None 判断是否已建目录）
    assert "wind_get_stock_quote" in wtp._by_name
    assert wtp._index["get_stock_quote"] == "stock_data"


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
