"""MCP 工具来源：作为 MCP 客户端经 stdio 拉起 mcp_server，把 FastMCP 工具直译为中立 ToolSpec。

整个会话复用一条 MCP 连接。工具返回 {code,msg,row_count,data} 是 HTTP 200 的正常字典，
code!=0 也作为非错误结果交回 LLM（由 LLM 读友好 msg 自行调整），保留 mcp_server 既有语义。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import Tool as MCPTool

from demomcp.interfaces.types import ToolResult, ToolSpec


def to_tool_spec(tool: MCPTool) -> ToolSpec:
    """把 mcp.types.Tool 直译为中立 ToolSpec（name / description / inputSchema）。"""
    return ToolSpec(name=tool.name, description=tool.description or "", input_schema=tool.inputSchema)


class MCPToolProvider:
    def __init__(self, session: Any, *, timeout: float = 30.0, retries: int = 2) -> None:
        # session 是 mcp.ClientSession；用 Any 是为了测试注入鸭子类型的假 session，
        # 同时真 ClientSession（mcp_client 工厂）也满足。
        self._session = session
        self._timeout = timeout
        self._retries = retries

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._session.list_tools()
        return [to_tool_spec(t) for t in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """调用工具：带读超时 + 重试。仅重试『抛出的异常』（transport/timeout）；
        isError 是业务结果，正常返回不重试（避免「无权限/需提升积分」被反复重试）。"""
        args = arguments or {}
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                result = await self._session.call_tool(
                    name, args, read_timeout_seconds=timedelta(seconds=self._timeout)
                )
            except Exception as exc:  # noqa: BLE001 - 工具层吞掉一切，转 is_error 保循环存活
                last_exc = exc
                if attempt < self._retries - 1:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                return ToolResult(
                    content=f"Error calling {name} after {self._retries} tries: {exc}", is_error=True
                )
            parts = [b.text for b in result.content if getattr(b, "type", None) == "text"]
            return ToolResult(content="\n".join(parts) or "OK", is_error=bool(result.isError))
        return ToolResult(content=f"Error calling {name}: {last_exc}", is_error=True)


@asynccontextmanager
async def mcp_tool_provider(
    params: StdioServerParameters, *, timeout: float = 30.0, retries: int = 2
) -> AsyncIterator[MCPToolProvider]:
    """拉起 mcp_server stdio 子进程并复用一条 MCP 连接，整个 asyncio 会话内有效。"""
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield MCPToolProvider(session, timeout=timeout, retries=retries)
