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
from demomcp.graph.routes import make_route_after_tool_rag, route_after_router
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
    skills: list | None = None,
    curate=None,
    max_iterations: int = 10,
    no_progress_cap: int = 2,
) -> object:
    """编译状态机，返回 langgraph CompiledStateGraph。

    skills：可选的投研报告 skill 注册表（router 据此命中报告 skill，synthesizer 用对应提示词）；None/空则纯意图路径。
    curate：可选 `(specs, query) -> list[ToolSpec]` 工具目录裁剪器；None 表示不裁剪（全量进 LLM）。
    max_iterations：agentic tool loop 上限——`tool_rag` 可通过条件边自环反复进入，直到 LLM 判定数据已足够
    （返回无 tool_uses）或执行轮数达上限才收尾生成/兜底。默认 10，与 `Settings.max_iterations` 对齐。
    no_progress_cap：进展守卫——某工具轮未新增任何证据/检索时视为无进展，连续达该值即终止循环
    （防「无权限/无数据/查不到」时反复空转烧满 max_iterations 造成死循环）。默认 2。
    """
    g = StateGraph(GraphState)
    g.add_node("router", make_router(llm, max_tokens=max_tokens, skills=skills))
    g.add_node("rewrite_query", make_rewrite_query(llm, max_tokens=max_tokens))
    g.add_node(
        "tool_rag",
        make_tool_rag(llm, tools, tool_defs, max_tokens=max_tokens, base_system=base_system, retriever=retriever, skills=skills, curate=curate, max_iterations=max_iterations, no_progress_cap=no_progress_cap),
    )
    g.add_node("synthesizer", make_synthesizer(llm, max_tokens=max_tokens, disclaimer=disclaimer, base_system=base_system, skills=skills))
    g.add_node("fallback", make_fallback(disclaimer=disclaimer, skills=skills))

    g.add_edge(START, "router")
    g.add_conditional_edges("router", route_after_router, {"rewrite_query": "rewrite_query", "fallback": "fallback"})
    g.add_edge("rewrite_query", "tool_rag")
    g.add_conditional_edges(
        "tool_rag",
        make_route_after_tool_rag(max_iterations),
        {"tool_rag": "tool_rag", "synthesizer": "synthesizer", "fallback": "fallback"},
    )
    g.add_edge("synthesizer", END)
    g.add_edge("fallback", END)

    return g.compile()
