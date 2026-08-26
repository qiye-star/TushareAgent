"""LangGraph 四节点编排：router → tool_rag → synthesizer / fallback。

- `build_research_graph`：构图入口（闭包注入 llm/tools/tool_defs/免责声明/domain 基础提示）。
- `GraphState`：状态类型。
"""

from __future__ import annotations

from demomcp.graph.builder import build_research_graph
from demomcp.graph.state import GraphState

__all__ = ["GraphState", "build_research_graph"]
