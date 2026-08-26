"""核心编排层：LangGraph 四节点状态机（Router → Tool/RAG → Synthesizer / Fallback）的薄壳。

`Agent` 只依赖 interfaces 的 ToolProvider 与 LLMClient（经 `demomcp.graph.builder` 构图闭包注入）；
`run` 保持原有外观（history + on_text/on_thinking/on_tool），内部经 LangGraph `ainvoke` 驱动，
把图结果归一化为 `AgentResult`（与旧手动循环契约一致，cli/web 零改动）。
"""

from __future__ import annotations

from typing import Any

from demomcp.config.settings import Settings
from demomcp.graph.builder import build_research_graph
from demomcp.graph.state import GraphState
from demomcp.interfaces.llm_client import LLMClient
from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import AgentResult


class Agent:
    def __init__(self, *, llm: LLMClient, tools: ToolProvider, config: Settings) -> None:
        self._llm = llm
        self._tools = tools
        self._config = config
        self._tool_defs: list[Any] | None = None
        self._graph: Any | None = None

    async def _get_tool_defs(self) -> list[Any]:
        if self._tool_defs is None:
            self._tool_defs = await self._tools.list_tools()
        return self._tool_defs

    async def _get_graph(self) -> Any:
        if self._graph is None:
            tool_defs = await self._get_tool_defs()
            self._graph = build_research_graph(
                self._llm,
                self._tools,
                tool_defs,
                max_tokens=self._config.ds_max_tokens,
                disclaimer=self._config.disclaimer,
                base_system=self._config.system_prompt,
            )
        return self._graph

    async def run(
        self,
        user_input: str,
        *,
        history: list[dict[str, Any]] | None = None,
        on_text=None,
        on_thinking=None,
        on_tool=None,
    ) -> AgentResult:
        messages = list(history or []) + [{"role": "user", "content": user_input}]
        state: GraphState = {
            "messages": messages,
            "original_query": user_input,
            "out_of_scope": False,
            "tool_results": [],
            "evidence": [],
            "rag_chunks": [],
            "citations": [],
            "usage": None,
            "stopped_reason": "end_turn",
        }
        graph = await self._get_graph()
        try:
            result = await graph.ainvoke(
                state,
                config={"configurable": {"on_text": on_text, "on_thinking": on_thinking, "on_tool": on_tool}},
            )
        except Exception as exc:  # noqa: BLE001 - 图异常（含 ExceptionGroup）归一为优雅结果；不捕 BaseException(取消)
            return AgentResult(
                final_text=f"处理失败：{type(exc).__name__}。请稍后重试。\n\n{self._config.disclaimer}",
                stopped_reason="error",
                messages=messages,
                tool_results=[],
                usage=None,
            )
        return AgentResult(
            final_text=result.get("final_answer") or "",
            stopped_reason=result.get("stopped_reason") or "end_turn",
            messages=result.get("messages") or messages,
            tool_results=result.get("tool_results") or [],
            usage=result.get("usage"),
            citations=result.get("citations") or [],
        )
