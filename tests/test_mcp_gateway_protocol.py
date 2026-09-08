"""网关真正对外说 MCP 协议的验证：起一个真实 uvicorn 实例（127.0.0.1 回环端口），用 demomcp 自己的
`MCPToolProvider`/`mcp_tool_provider`（跟 demomcp 连官方 Tushare MCP 用的是同一段代码）当真实 MCP
客户端连 `/mcp`，验证 list_tools/call_tool 走完整 streamable-http 协议往返——不是只测 Python 内部调用。

这是唯一一处需要真实 socket 的网关测试（其它测试走 FastAPI TestClient 的 ASGI transport 或纯 Python
逻辑）；固定测试端口（18766），与仓库 docker-compose 的 8766 生产端口不同，避免本机同时开发时撞车。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn

from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.mcp import mcp_tool_provider

_TEST_PORT = 18766


class _FakeProvider:
    async def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="echo",
                description="echo back the input",
                input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
            )
        ]

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        return ToolResult(content=f"echo:{(arguments or {}).get('msg')}")


def _fake_sources(settings):
    from mcp_gateway.sources import SourceDef

    @asynccontextmanager
    async def connect():
        yield _FakeProvider()

    return [SourceDef(id="fake", display_name="Fake Source", connect=connect)]


async def test_real_streamable_http_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_PORT", str(_TEST_PORT))

    import mcp_gateway.app as gw_app

    monkeypatch.setattr(gw_app, "build_sources", _fake_sources)

    config = uvicorn.Config(gw_app.app, host="127.0.0.1", port=_TEST_PORT, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if getattr(server, "started", False):
                break
            await asyncio.sleep(0.05)
        assert server.started

        async with mcp_tool_provider(f"http://127.0.0.1:{_TEST_PORT}/mcp", timeout=10.0) as provider:
            specs = await provider.list_tools()
            assert [s.name for s in specs] == ["echo"]

            result = await provider.call_tool("echo", {"msg": "hi"})
            assert result.is_error is False
            assert result.content == "echo:hi"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10.0)
