"""条件边决策：router / tool_rag 之后的走向。

`make_route_after_tool_rag` 闭包捕获 `max_iterations`：决定 tool_rag 之后是自环继续取数、
进入 synthesizer 生成、还是 fallback 兜底（agentic tool loop）。
"""

from __future__ import annotations

from demomcp.graph.state import GraphState


def route_after_router(state: GraphState) -> str:
    """越界 → fallback；否则进入 rewrite_query（改写后再进 tool_rag）。"""
    return "fallback" if state.get("out_of_scope") else "rewrite_query"


def make_route_after_tool_rag(max_iterations: int):
    """闭包捕获 max_iterations：tool_rag 之后的分支选择（支持自环回 tool_rag）。

    优先级：硬失败(有 fallback_reason) → fallback；LLM 还要取数且未到上限 → 回环；
    无任何可用证据 → fallback；否则（已足够 / 到上限且有证据）→ synthesizer。
    """

    def route(state: GraphState) -> str:
        if state.get("fallback_reason"):
            return "fallback"  # 硬失败（node_error/parallel_race/无证据）→ 兜底
        if state.get("want_more") and int(state.get("loop_index") or 0) < max_iterations:
            return "tool_rag"  # LLM 还要取数且未到上限 → 自环
        if not state.get("evidence") and not state.get("rag_chunks"):
            return "fallback"  # 无任何可用证据 → 兜底
        return "synthesizer"  # 已足够 / 到上限且有证据 → 生成

    return route
