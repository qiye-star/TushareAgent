"""工具来源离线测试：MCP Tool 直译 + call_tool 错误/异常处理。"""

from __future__ import annotations

from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool

from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.composite import CompositeToolProvider
from demomcp.providers.tools.mcp import MCPToolProvider, to_tool_spec


class _FakeProvider:
    """哑 ToolProvider：list_tools 返回预设 specs，call_tool 走预设 handler。"""

    def __init__(self, specs, handler) -> None:
        self._specs = specs
        self._handler = handler

    async def list_tools(self) -> list[ToolSpec]:
        return self._specs

    async def call_tool(self, name, arguments=None) -> ToolResult:
        return self._handler(name, arguments)


def test_to_tool_spec_maps_mcp_tool() -> None:
    tool = MCPTool(name="query", description="desc", inputSchema={"type": "object"})
    spec = to_tool_spec(tool)
    assert spec.name == "query"
    assert spec.description == "desc"
    assert spec.input_schema == {"type": "object"}


class _FakeSession:
    def __init__(self, *, error: bool = False) -> None:
        self._error = error

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
        return CallToolResult(
            content=[TextContent(type="text", text="row1\nrow2")], isError=self._error
        )


async def test_mcp_call_tool_returns_joined_text() -> None:
    provider = MCPToolProvider(_FakeSession())
    result = await provider.call_tool("query", {"x": 1})
    assert result.content == "row1\nrow2"
    assert result.is_error is False


async def test_mcp_call_tool_error_flag() -> None:
    provider = MCPToolProvider(_FakeSession(error=True))
    result = await provider.call_tool("query", {})
    assert result.is_error is True


async def test_mcp_call_tool_raises_becomes_iserror() -> None:
    class _Boom:
        async def call_tool(self, name, arguments=None, read_timeout_seconds=None):
            raise RuntimeError("transport down")

    provider = MCPToolProvider(_Boom())
    result = await provider.call_tool("query", {})
    assert result.is_error is True
    assert "Error calling" in result.content


async def test_composite_merges_and_dispatches_by_full_name() -> None:
    tushare = _FakeProvider(
        [ToolSpec("query", description="ts")], lambda n, a: ToolResult(content=f"ts:{n}", is_error=False)
    )
    wind = _FakeProvider(
        [ToolSpec("wind_get_stock_quote", description="wind")],
        lambda n, a: ToolResult(content=f"wind:{n}", is_error=False),
    )
    provider = CompositeToolProvider([tushare, wind])
    specs = await provider.list_tools()
    assert [s.name for s in specs] == ["query", "wind_get_stock_quote"]
    # 按全名分发：query 走 Tushare、wind_* 走万得（子 provider 收到的是带前缀全名）
    assert (await provider.call_tool("query", {})).content == "ts:query"
    assert (await provider.call_tool("wind_get_stock_quote", {})).content == "wind:wind_get_stock_quote"


async def test_composite_unknown_tool_is_error() -> None:
    provider = CompositeToolProvider([_FakeProvider([ToolSpec("query")], lambda n, a: ToolResult("ok"))])
    result = await provider.call_tool("nope", {})
    assert result.is_error is True
    assert "Unknown tool" in result.content
