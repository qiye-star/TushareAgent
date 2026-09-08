"""工具名 → 上游源类别（快报侧自持）。

网关对快报是一个**不透明的 MCP 端点**：`call_tool` 只有工具名，没有 source_id
（`mcp_gateway/pool.py::_name_to_source` 是网关进程私有状态，不出现在 MCP 协议里）。
所以只能按工具名分类，规则与 `agents/agent.py::base_system_for` 判断「本轮有哪些源」同一套
——常量都取自 `interfaces/types.py`，全仓唯一一份，不在这里重新声明。

为什么需要它：**五个源的「成功」约定互相矛盾**，一条连接上流着混合信封。
- Tushare：`{"code": 0, ...}` 成功（也常见直接返回裸数组）
- iFind：`{"code": 1, "msg": "success", ...}` 成功——**与 Tushare 恰好相反**
- Wind：没有 `code` 字段即成功
- 免费源：无 `code` 约定，失败是 `{"error": "..."}`
沿用单一的 `code != 0` 判据会把 iFind 的每一次成功都当失败
（实测 demomcp→网关那一跳因此把调用从 0.65s 放大到 6.20s 并双倍消耗 iFind 的并发配额）。

刻意**不 import** `demomcp.agents.*`：`agent.py` 会拉进 LangGraph，
破坏 `quickreport/__init__.py` 承诺的「与 Agent/LLM 主链路解耦」。
"""

from __future__ import annotations

from demomcp.interfaces.types import (
    FREE_SOURCE_TOOLS,
    IFIND_TOOL_PREFIX,
    WIND_TOOL_PREFIX,
)

KIND_TUSHARE = "tushare"
KIND_WIND = "wind"
KIND_IFIND = "ifind"
KIND_FREE = "free"
KIND_PIPELINE = "pipeline"  # 非工具来源（段级超时/管线自身错误），provenance 用

# 人话标签：前端 provenance 直接显示，与 `mcp_gateway/sources.py` 的 display_name 对齐
KIND_LABELS: dict[str, str] = {
    KIND_TUSHARE: "Tushare 官方 MCP",
    KIND_WIND: "万得 Wind",
    KIND_IFIND: "同花顺 iFind",
    KIND_FREE: "免费源（AkShare/财经新闻）",
    KIND_PIPELINE: "快报管线",
}


def source_kind(tool: str) -> str:
    """工具名 → 源类别。

    兜底 `tushare`：官方源用原生接口名、无前缀，且数量最大（实测 248 个），
    未知名字当 Tushare 判定与改造前行为一致（最小意外原则）。
    """
    if tool.startswith(WIND_TOOL_PREFIX):
        return KIND_WIND
    if tool.startswith(IFIND_TOOL_PREFIX):
        return KIND_IFIND
    if tool in FREE_SOURCE_TOOLS:
        return KIND_FREE
    return KIND_TUSHARE


def source_label(kind: str) -> str:
    """源类别 → 展示名；未知类别原样返回（不编造）。"""
    return KIND_LABELS.get(kind, kind)
