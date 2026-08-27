"""构图：把四节点串成 LangGraph 状态图并 compile。

llm / tools / tool_defs / base_system / disclaimer 在构建时以闭包注入；
运行期仅经 ainvoke 的 config.configurable 传回调（on_text/on_thinking/on_tool）。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from demomcp.graph.nodes import (
    make_fallback,
    make_rewrite_query,
    make_router,
    make_synthesizer,
    make_tool_rag,
)
from demomcp.graph.routes import route_after_router, route_after_tool_rag
from demomcp.graph.state import GraphState


def build_research_graph(
    llm,
    tools,
    tool_defs: list,
    *,
    max_tokens: int,
    disclaimer: str,
    base_system: str = "",
    retriever=None,
) -> object:
    """编译四节点状态机，返回 langgraph CompiledStateGraph。"""
    g = StateGraph(GraphState)
    g.add_node("router", make_router(llm, max_tokens=max_tokens))
    g.add_node("rewrite_query", make_rewrite_query(llm, max_tokens=max_tokens))
    g.add_node(
        "tool_rag",
        make_tool_rag(llm, tools, tool_defs, max_tokens=max_tokens, base_system=base_system, retriever=retriever),
    )
    g.add_node("synthesizer", make_synthesizer(llm, max_tokens=max_tokens, disclaimer=disclaimer, base_system=base_system))
    g.add_node("fallback", make_fallback(disclaimer=disclaimer))

    g.add_edge(START, "router")
    g.add_conditional_edges("router", route_after_router, {"rewrite_query": "rewrite_query", "fallback": "fallback"})
    g.add_edge("rewrite_query", "tool_rag")
    g.add_conditional_edges("tool_rag", route_after_tool_rag, {"synthesizer": "synthesizer", "fallback": "fallback"})
    g.add_edge("synthesizer", END)
    g.add_edge("fallback", END)

    return g.compile()
