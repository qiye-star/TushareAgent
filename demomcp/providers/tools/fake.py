"""假工具来源：离线测试用，可返回预设工具并记录每次调用。"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.types import ToolResult, ToolSpec


class FakeToolProvider:
    def __init__(
        self,
        tools: list[ToolSpec],
        call_results: dict[str, ToolResult] | None = None,
        *,
        raise_on: set[str] | None = None,
    ) -> None:
        self._tools = tools
        self._call_results = call_results or {}
        self._raise_on = raise_on or set()
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def list_tools(self) -> list[ToolSpec]:
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        self.calls.append((name, arguments))
        if name in self._raise_on:
            raise RuntimeError(f"boom: {name}")
        return self._call_results.get(name, ToolResult(content="ok", is_error=False))
