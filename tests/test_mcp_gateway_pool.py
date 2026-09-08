"""网关核心聚合逻辑：GatewayToolProvider 按已启用源聚合 list_tools/call_tool，开关切换即时生效；
SourcePool 热启动指数退避重试。全部用假 ToolProvider + 假 SourceDef.connect，不联网。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import mcp_gateway.pool as pool_mod
from demomcp.interfaces.types import ToolResult, ToolSpec
from mcp_gateway.pool import GatewayToolProvider, SourcePool
from mcp_gateway.sources import SourceDef


class _FakeProvider:
    def __init__(self, name: str, tool_name: str) -> None:
        self.name = name
        self.tool_name = tool_name
        self.calls: list[tuple[str, dict | None]] = []

    async def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name=self.tool_name, description=f"{self.name} tool")]

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        self.calls.append((name, arguments))
        return ToolResult(content=f"{self.name}:{name}:ok")


def _fake_source(source_id: str, tool_name: str, *, default_enabled: bool = True):
    provider = _FakeProvider(source_id, tool_name)

    @asynccontextmanager
    async def connect():
        yield provider

    return SourceDef(id=source_id, display_name=source_id, connect=connect, default_enabled=default_enabled), provider


async def test_list_tools_only_returns_enabled_sources(tmp_path) -> None:
    src_a, _ = _fake_source("a", "tool_a")
    src_b, _ = _fake_source("b", "tool_b")
    gw = GatewayToolProvider([src_a, src_b], data_dir=str(tmp_path))

    specs = await gw.list_tools()
    assert {s.name for s in specs} == {"tool_a", "tool_b"}

    await gw.set_enabled("b", False)
    specs = await gw.list_tools()
    assert {s.name for s in specs} == {"tool_a"}


async def test_call_tool_routes_to_owning_source(tmp_path) -> None:
    src_a, prov_a = _fake_source("a", "tool_a")
    src_b, prov_b = _fake_source("b", "tool_b")
    gw = GatewayToolProvider([src_a, src_b], data_dir=str(tmp_path))
    await gw.list_tools()

    result = await gw.call_tool("tool_b", {"x": 1})
    assert result.content == "b:tool_b:ok"
    assert prov_b.calls == [("tool_b", {"x": 1})]
    assert prov_a.calls == []


async def test_call_tool_disabled_source_returns_clear_error(tmp_path) -> None:
    src_a, _ = _fake_source("a", "tool_a")
    gw = GatewayToolProvider([src_a], data_dir=str(tmp_path))
    await gw.list_tools()
    await gw.set_enabled("a", False)

    result = await gw.call_tool("tool_a", {})
    assert result.is_error is True
    assert "已停用" in result.content


async def test_call_tool_unknown_name_returns_error(tmp_path) -> None:
    src_a, _ = _fake_source("a", "tool_a")
    gw = GatewayToolProvider([src_a], data_dir=str(tmp_path))
    await gw.list_tools()

    result = await gw.call_tool("does_not_exist", {})
    assert result.is_error is True
    assert "Unknown tool" in result.content


async def test_set_enabled_persists_across_new_instance(tmp_path) -> None:
    src_a, _ = _fake_source("a", "tool_a")
    gw1 = GatewayToolProvider([src_a], data_dir=str(tmp_path))
    await gw1.set_enabled("a", False)

    src_a2, _ = _fake_source("a", "tool_a")
    gw2 = GatewayToolProvider([src_a2], data_dir=str(tmp_path))
    statuses = await gw2.status()
    assert statuses[0]["enabled"] is False


async def test_set_enabled_unknown_id_returns_false(tmp_path) -> None:
    src_a, _ = _fake_source("a", "tool_a")
    gw = GatewayToolProvider([src_a], data_dir=str(tmp_path))
    ok = await gw.set_enabled("nope", False)
    assert ok is False


async def test_cross_source_name_collision_keeps_first_declared(tmp_path, caplog) -> None:
    """跨源撞名：先声明的源赢，重复那条整个丢掉（不能让 llm.chat(tools=…) 收到两个同名工具），
    并留一条告警——真出现就说明某源换了命名，得去 sources.py 给它加前缀。"""
    src_a, prov_a = _fake_source("a", "same_name")
    src_b, prov_b = _fake_source("b", "same_name")
    gw = GatewayToolProvider([src_a, src_b], data_dir=str(tmp_path))

    with caplog.at_level("WARNING", logger="mcp_gateway.pool"):
        specs = await gw.list_tools()

    assert [s.name for s in specs] == ["same_name"]  # 去重，不是两条
    assert "工具名冲突" in caplog.text

    result = await gw.call_tool("same_name", {})
    assert result.content == "a:same_name:ok"  # 路由到先声明的 a
    assert prov_b.calls == []
    assert prov_a.calls == [("same_name", {})]


async def test_hot_start_retries_until_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pool_mod, "HOT_START_BACKOFF_BASE", 0.01)
    monkeypatch.setattr(pool_mod, "HOT_START_BACKOFF_MAX", 0.01)
    attempts = {"n": 0}

    @asynccontextmanager
    async def flaky_connect():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("boom")
        yield _FakeProvider("a", "tool_a")

    src = SourceDef(id="a", display_name="a", connect=flaky_connect)
    pool = SourcePool(src, enabled=True, data_dir=str(tmp_path))
    await pool.hot_start()

    assert attempts["n"] == 3
    assert pool.connected is True


async def test_hot_start_skips_disabled_source(tmp_path) -> None:
    connected = {"n": 0}

    @asynccontextmanager
    async def connect():
        connected["n"] += 1
        yield _FakeProvider("a", "tool_a")

    src = SourceDef(id="a", display_name="a", connect=connect)
    pool = SourcePool(src, enabled=False, data_dir=str(tmp_path))
    await pool.hot_start()

    assert connected["n"] == 0
    assert pool.connected is False


async def test_status_reports_disconnected_before_first_use(tmp_path) -> None:
    src_a, _ = _fake_source("a", "tool_a")
    gw = GatewayToolProvider([src_a], data_dir=str(tmp_path))
    statuses = await gw.status()
    assert statuses == [{"id": "a", "display_name": "a", "enabled": True, "connected": False, "tool_count": None}]


async def test_toggle_off_then_on_releases_and_reconnects(tmp_path) -> None:
    src_a, _ = _fake_source("a", "tool_a")
    gw = GatewayToolProvider([src_a], data_dir=str(tmp_path))
    await gw.list_tools()  # 建连
    assert gw.pools["a"].connected is True

    await gw.set_enabled("a", False)
    # set_enabled 关闭分支同步摘池（await set_enabled 内部先摘池再 create_task 后台关闭）
    assert gw.pools["a"].connected is False

    await gw.set_enabled("a", True)
    # 打开分支是 fire-and-forget 重连任务，给它一点时间跑完
    for _ in range(50):
        if gw.pools["a"].connected:
            break
        await asyncio.sleep(0.02)
    assert gw.pools["a"].connected is True
