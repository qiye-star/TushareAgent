"""工具供给协议：agent 循环只依赖它，具体实现（MCP / 假实现 / 未来自有工具）皆可注入。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from demomcp.interfaces.types import ToolResult, ToolSpec


@runtime_checkable
class ToolProvider(Protocol):
    async def list_tools(self) -> list[ToolSpec]:
        """返回本轮可用的中立项工具集（静态，可缓存）。"""
        ...

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        """执行一次工具调用；必需吞掉异常并转为 is_error=True 的结果，避免循环崩溃。"""
        ...
