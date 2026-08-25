"""工具注册 / 分发：把多来源 ToolProvider 合并成一个，作为「加工具」的挂载点。"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import ToolResult, ToolSpec


class CompositeToolProvider:
    """合并多个 ToolProvider：list_tools 汇总、call_tool 按名分发到首个提供该工具的 provider。"""

    def __init__(self, providers: list[ToolProvider]) -> None:
        self._providers = providers
        self._by_name: dict[str, ToolProvider] = {}
        self._specs: list[ToolSpec] | None = None

    async def list_tools(self) -> list[ToolSpec]:
        if self._specs is None:
            specs: list[ToolSpec] = []
            by_name: dict[str, ToolProvider] = {}
            for provider in self._providers:
                for spec in await provider.list_tools():
                    specs.append(spec)
                    by_name.setdefault(spec.name, provider)
            self._specs = specs
            self._by_name = by_name
        return list(self._specs)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        provider = self._by_name.get(name)
        if provider is None:
            return ToolResult(content=f"Unknown tool: {name}", is_error=True)
        return await provider.call_tool(name, arguments)
