"""多来源合并为一个 :class:`ToolProvider` 的工具 / 分发挂载点。

`demomcp/agents/registry.py` 里有一份同名废弃占位（无任何引用，`docs/ARCHITECTURE.md` 已标明不在当前代码）。
为守 CLAUDE.md 的 `providers → interfaces` 单向依赖，这里在 providers 层放正式实现：把多个 `ToolProvider`
合并成一个，`list_tools` 汇总、`call_tool` 按工具全名分发到首个提供该工具的 provider。
"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import ToolResult, ToolSpec


class CompositeToolProvider:
    """合并多个 ToolProvider：list_tools 汇总、call_tool 按名分发（setdefault —— 先到时先得）。

    子 provider 可自带命名空间（如万得的 `wind_` 前缀）：`call_tool` 会把**带前缀的全名**原样传给
    注册该名字的 provider，由 provider 自行剥前缀路由。
    """

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
