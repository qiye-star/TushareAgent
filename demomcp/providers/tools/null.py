"""空工具供给：MCP 全局开关关闭时注入给 Agent，使其退化为纯 LLM 聊天。

`list_tools()` 恒返回 []：Agent 经 `_get_tool_defs` 拿到空工具定义后，`tool_rag` 节点
`llm.chat(tools=[])` 自然不带任何函数定义、LLM 不会产生 tool_calls，图走既有的
「无 tool_calls → 零证据直接作答」通道（见 CLAUDE.md「直接作答」小节），无需改动 graph/nodes.py。
`call_tool` 理论上不会被调用（没有工具可选），保留是为了满足 ToolProvider 协议、防御性兜底。
"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.types import ToolResult, ToolSpec

_DISABLED_MSG = "MCP 数据源当前已停用（管理员在设置页关闭），无法调用取数工具，请直接凭已有知识回答或提示用户先开启数据源。"


class NullToolProvider:
    async def list_tools(self) -> list[ToolSpec]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        return ToolResult(content=_DISABLED_MSG, is_error=True)
