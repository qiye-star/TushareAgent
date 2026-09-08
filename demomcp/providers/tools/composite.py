"""多来源合并为一个 :class:`ToolProvider` 的工具 / 分发挂载点。

`demomcp/agents/registry.py` 里有一份同名废弃占位（无任何引用，`docs/ARCHITECTURE.md` 已标明不在当前代码）。
为守 CLAUDE.md 的 `providers → interfaces` 单向依赖，这里在 providers 层放正式实现：把多个 `ToolProvider`
合并成一个，`list_tools` 汇总、`call_tool` 按工具全名分发到首个提供该工具的 provider。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import ToolResult, ToolSpec

logger = logging.getLogger("demomcp.composite")


class CompositeToolProvider:
    """合并多个 ToolProvider：list_tools 汇总、call_tool 按名分发（setdefault —— 先到时先得）。

    子 provider 可自带命名空间（如万得的 `wind_` 前缀）：`call_tool` 会把**带前缀的全名**原样传给
    注册该名字的 provider，由 provider 自行剥前缀路由。
    """

    def __init__(self, providers: list[ToolProvider]) -> None:
        self._providers = providers
        self._by_name: dict[str, ToolProvider] = {}
        self._specs: list[ToolSpec] | None = None
        # 单飞 + 双检：并发 list_tools（热启动 warm_up 与首个 /chat）曾把「全量 _specs + 部分 _by_name」
        # 错配——call_tool 报 Unknown tool 而 list_tools 仍广告全量（2026-09-07 审查）
        self._lock = asyncio.Lock()

    async def list_tools(self) -> list[ToolSpec]:
        """汇总各来源清单；单来源失败不拖垮整体（同 wind.py 按域隔离的先例）。

        只有全部来源成功才缓存 `_specs`（保持「一次成功、永久缓存」语义不变）；任一来源失败则本轮
        不缓存（下次调用会重新尝试失败的那个来源），但 `_by_name` 按本轮实际拿到的结果更新，
        保证同一请求内后续 call_tool 分发不受影响。锁 + 双检：并发建目录只建一次。
        """
        if self._specs is not None:
            return list(self._specs)
        async with self._lock:
            if self._specs is not None:  # 双检：等锁期间另一协程已建好缓存
                return list(self._specs)
            specs: list[ToolSpec] = []
            by_name: dict[str, ToolProvider] = {}
            all_ok = True
            for provider in self._providers:
                try:
                    provider_specs = await provider.list_tools()
                except Exception as exc:  # noqa: BLE001 - 单来源失败不拖垮整体清单，仅本轮不缓存
                    all_ok = False
                    logger.warning("工具来源 %s list_tools 失败，本轮跳过：%s", type(provider).__name__, exc)
                    continue
                for spec in provider_specs:
                    specs.append(spec)
                    by_name.setdefault(spec.name, provider)
            if all_ok:
                self._specs = specs
                self._by_name = by_name
            elif self._specs is None:
                # 首次构建部分失败：把本轮拿到的合并进路由表（不整体覆盖，别丢更完整的旧登记），
                # 保证同请求内 call_tool 分发可用；有全量缓存时（锁内不可能，防御）不覆盖
                for name, provider in by_name.items():
                    self._by_name.setdefault(name, provider)
            return specs

    async def warm_up(self) -> None:
        """热启动：先预热带 warm_up 的子 provider（如 Tushare 连一次工具清单、Wind 预连 7 域），
        再预填本丛集缓存（首次 /chat 的 list_tools 零等待）。"""
        for provider in self._providers:
            fn = getattr(provider, "warm_up", None)
            if fn is not None:
                await fn()
        await self.list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        provider = self._by_name.get(name)
        if provider is None:
            return ToolResult(content=f"Unknown tool: {name}", is_error=True)
        return await provider.call_tool(name, arguments)
