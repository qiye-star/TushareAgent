"""条件边决策：router / tool_rag 之后的走向。"""

from __future__ import annotations

from demomcp.graph.state import GraphState


def route_after_router(state: GraphState) -> str:
    """越界 → fallback；否则进入 tool_rag。"""
    return "fallback" if state.get("out_of_scope") else "tool_rag"


def route_after_tool_rag(state: GraphState) -> str:
    """无可用证据（有 fallback_reason）→ fallback；否则进入 synthesizer。"""
    return "fallback" if state.get("fallback_reason") else "synthesizer"
