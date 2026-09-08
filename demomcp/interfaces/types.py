"""契约层的数据类型：agent 循环与外部 LLM / 工具实现之间传递的中立结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 万得（Wind）懒发现三件套：镜像 Tushare 的 list_apis/get_api_info/query，
# 让 WindToolProvider 只暴露这 3 个合成元工具，而非直接摊开 35 个具体 wind_* schema。
WIND_LIST_APIS = "wind_list_apis"
WIND_GET_API_INFO = "wind_get_api_info"
WIND_QUERY = "wind_query"
WIND_META_TOOL_NAMES: frozenset[str] = frozenset({WIND_LIST_APIS, WIND_GET_API_INFO, WIND_QUERY})

# 恒保留在 agent 工具面里的「发现/兜底」工具：Tushare 的 `query` 是未被揭示接口的逃生通道；
# 万得同理三件套。未装配万得时这 3 个名字不会出现在 tool_defs 里，对应 select_tools 也就选不到。
META_TOOL_NAMES: frozenset[str] = (
    frozenset({"list_apis", "get_api_info", "query", "stock_basic"}) | WIND_META_TOOL_NAMES
)


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
    citations: list[str] = field(default_factory=list)
    structured: dict[str, Any] | None = None
    mode: str = "agent"  # "agent" | "quick"；Agent.run() 按调用时的 mode 打上，标记这轮产出方式
