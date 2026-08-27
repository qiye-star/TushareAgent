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
    rewritten_query: str                  # rewrite_query 节点产出（来源+年份+扩词），供 RAG 检索用

    # Router 产出
    intent: str | None              # "market" | "report" | "compare"
    out_of_scope: bool

    # Tool/RAG 产出
    retrieval_plan: list[dict[str, Any]]   # 本轮拟调用的工具（id/name/arguments）
    tool_results: list[ToolResult]
    evidence: list[dict[str, Any]]         # [{source_type, source, content, cite?, params?}]（工具+rag 证据）
    rag_chunks: list[Any]                  # RagChunk（来自 retriever.retrieve）
    request_params: dict[str, Any] | None  # 归一化/校验后的实际请求参数（ts_code/日期/复权/期次）
    validation_errors: list[str]           # 工具返回的友好校验失败（first-class）

    # Synthesizer / Fallback 产出
    final_answer: str | None
    citations: list[str]                   # 引用到的来源名（工具名/文档标题）
    structured: dict[str, Any] | None      # 结构化输出（answer/claims/citations 元数据）
    fallback_reason: str | None
    usage: dict[str, Any] | None
    stopped_reason: str
