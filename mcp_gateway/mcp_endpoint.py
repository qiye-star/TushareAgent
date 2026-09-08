"""网关自己对外的 MCP server：`mcp.server.lowlevel.Server` + `StreamableHTTPSessionManager`。

`@server.list_tools()`/`@server.call_tool()` 是纯异步 handler，每次调用现查 `GatewayToolProvider`
（内部按「当前已启用的源」聚合），天然动态、无需重启网关即可反映开关变化——这就是"按需取用"。

`StreamableHTTPSessionManager(app=server, stateless=True)`：stateless 模式每次请求都是全新
transport（无需维护会话状态/幂等重放），符合本网关「纯工具代理，不需要跨请求会话状态」的定位；
`.handle_request` 是标准 ASGI 可调用（`(scope, receive, send)`），挂进 app.py 的 Starlette 路由；
`.run()` 是要在应用生命周期里进入的异步上下文（起内部 task group），同样在 app.py 的 lifespan 里做。
"""

from __future__ import annotations

from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from demomcp.interfaces.types import ToolSpec
from mcp_gateway.pool import GatewayToolProvider


def to_mcp_tool(spec: ToolSpec) -> types.Tool:
    return types.Tool(
        name=spec.name,
        description=spec.description,
        inputSchema=spec.input_schema or {"type": "object", "properties": {}},
    )


def build_mcp_server(gateway: GatewayToolProvider) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("mcp-gateway")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        specs = await gateway.list_tools()
        return [to_mcp_tool(s) for s in specs]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        result = await gateway.call_tool(name, arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.content)],
            isError=result.is_error,
        )

    return server


def build_session_manager(gateway: GatewayToolProvider) -> StreamableHTTPSessionManager:
    server = build_mcp_server(gateway)
    return StreamableHTTPSessionManager(app=server, stateless=True)
