"""LangGraph State：四节点状态机（router → tool_rag → synthesizer / fallback）的心脏。

普通 list 追加，不用 add_messages reducer——messages 是 OpenAI 风格 dict 列表，
由调用方（Agent/AuthTurn 恢复）自行管理；状态只承载本轮跨节点的中间产物。
"""

from __future__ import annotations

from typing import Any, TypedDict

from demomcp.interfaces.types import ToolResult


class GraphState(TypedDict, total=False):
    # 对话上下文（OpenAI 风格 dict：user / assistant / tool）
    messages: list[dict[str, Any]]
    original_query: str

    # Router 产出
    intent: str | None              # "market" | "report" | "compare"
    out_of_scope: bool

    # Tool/RAG 产出
    retrieval_plan: list[dict[str, Any]]   # 本轮拟调用的工具（id/name/arguments）
    tool_results: list[ToolResult]
    evidence: list[dict[str, Any]]         # [{source, content}]（工具结果摘要；RAG 后续并入）
    rag_chunks: list[Any]

    # Synthesizer / Fallback 产出
    final_answer: str | None
    citations: list[str]                   # 引用到的来源（工具名）
    fallback_reason: str | None
    usage: dict[str, Any] | None
    stopped_reason: str
