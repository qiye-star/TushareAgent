"""MCP 工具重试/超时离线测试：仅重试抛出的异常，isError 业务结果不重试。"""

from __future__ import annotations

from datetime import timedelta

from mcp.types import CallToolResult, TextContent

from demomcp.providers.tools.mcp import MCPToolProvider


class _FlakySession:
    def __init__(self, fails: int = 2) -> None:
        self.fails = fails
        self.calls = 0
        self.timeouts: list[timedelta | None] = []

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls += 1
        self.timeouts.append(read_timeout_seconds)
        if self.calls <= self.fails:
            raise TimeoutError("slow")
        return CallToolResult(content=[TextContent(type="text", text="ok")], isError=False)


class _AlwaysFail:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls += 1
        raise TimeoutError("boom")


class _BusinessError:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        self.calls += 1
        return CallToolResult(content=[TextContent(type="text", text="无权限")], isError=True)


async def test_call_tool_retries_then_succeeds() -> None:
    s = _FlakySession(fails=2)
    provider = MCPToolProvider(s, timeout=30.0, retries=3)
    result = await provider.call_tool("query", {})
    assert result.is_error is False
    assert result.content == "ok"
    assert s.calls == 3  # 2 次失败 + 1 次成功
    assert all(t == timedelta(seconds=30) for t in s.timeouts)  # 超时报成 timedelta


async def test_call_tool_exhausts_retries() -> None:
    s = _AlwaysFail()
    provider = MCPToolProvider(s, timeout=5.0, retries=2)
    result = await provider.call_tool("query", {})
    assert result.is_error is True
    assert "after 2 tries" in result.content
    assert s.calls == 2


async def test_business_error_not_retried() -> None:
    s = _BusinessError()
    provider = MCPToolProvider(s, retries=3)
    result = await provider.call_tool("query", {})
    assert result.is_error is True
    assert result.content == "无权限"
    assert s.calls == 1  # isError 是业务结果，不重试
