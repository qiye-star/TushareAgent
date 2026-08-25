"""契约层的数据类型：agent 循环与外部 LLM / 工具实现之间传递的中立结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    """中立工具描述（由 MCP Tool 直译），各 LLM 实现负责适配成自家 wire 格式。"""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """一次工具调用的结果文本（喂给 LLM）。"""

    content: str
    is_error: bool = False


@dataclass
class ToolUse:
    """LLM 请求调用的一次工具。"""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    """一轮 LLM 回复的中立视图。

    raw_content 为 assistant 轮附加字段（如 content / tool_calls），
    由 LLM 实现提供并原样回传，供循环拼装 assistant 消息条目。
    """

    stop_reason: str
    tool_uses: list[ToolUse] = field(default_factory=list)
    raw_content: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    thinking: str = ""
    usage: dict[str, Any] | None = None


@dataclass
class AgentResult:
    """一次 agent 循环的结果。"""

    final_text: str
    stopped_reason: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    usage: dict[str, Any] | None = None
