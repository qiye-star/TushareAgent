"""核心编排层：LangGraph 四节点状态机（Router → Tool/RAG → Synthesizer / Fallback）的薄壳。

`Agent` 只依赖 interfaces 的 ToolProvider 与 LLMClient（经 `demomcp.graph.builder` 构图闭包注入）；
`run` 保持原有外观（history + on_text/on_thinking/on_tool），内部经 LangGraph `ainvoke` 驱动，
把图结果归一化为 `AgentResult`（与旧手动循环契约一致，cli/web 零改动）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from demomcp.config.logging import get_logger
from demomcp.config.settings import Settings
from demomcp.graph.builder import build_research_graph
from demomcp.graph.state import GraphState
from demomcp.interfaces.llm_client import LLMClient
from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import AgentResult

_log = get_logger("agents")


def _flatten_exceptions(err: BaseException) -> list[BaseException]:
    """把（可能嵌套的）ExceptionGroup/BaseExceptionGroup 拍平成叶子异常，便于取真实原因/判断取消。"""
    if isinstance(err, BaseExceptionGroup):
        out: list[BaseException] = []
        for e in err.exceptions:
            out.extend(_flatten_exceptions(e))
        return out
    return [err]


class Agent:
    def __init__(self, *, llm: LLMClient, tools: ToolProvider, config: Settings) -> None:
        self._llm = llm
        self._tools = tools
        self._config = config
        self._tool_defs: list[Any] | None = None
        self._graph: Any | None = None
        self._retriever: Any | None = None

    async def _get_tool_defs(self) -> list[Any]:
        if self._tool_defs is None:
            self._tool_defs = await self._tools.list_tools()
        return self._tool_defs

    async def _get_retriever(self) -> Any | None:
        """懒加载 + 兜底：RAG_HTTP_URL 时用 HTTP 远端检索（该进程不再打开 Milvus）；
        否则构建运行时 retriever，失败回退 None（图上不接 RAG，不崩）。"""
        if self._retriever is None:
            cfg = self._config
            if cfg.rag_http_url:
                from demomcp.rag.http_retriever import HttpRetriever

                self._retriever = HttpRetriever(
                    cfg.rag_http_url, timeout=cfg.rag_http_timeout, token=cfg.rag_http_token
                )
                return self._retriever
            try:
                from demomcp.rag.runtime import build_runtime_retriever

                self._retriever = await build_runtime_retriever(cfg)
            except Exception as exc:  # noqa: BLE001 - RAG 构建失败仅回退无检索
                print(f"[agent] RAG retriever 构建失败，回退无检索：{exc}")
                self._retriever = None
        return self._retriever

    async def _get_graph(self) -> Any:
        if self._graph is None:
            tool_defs = await self._get_tool_defs()
            retriever = await self._get_retriever()
            self._graph = build_research_graph(
                self._llm,
                self._tools,
                tool_defs,
                max_tokens=self._config.ds_max_tokens,
                disclaimer=self._config.disclaimer,
                base_system=self._config.system_prompt,
                retriever=retriever,
            )
        return self._graph

    def _error_result(self, messages: list[dict[str, Any]], cause: BaseException) -> AgentResult:
        self._log_error(cause)
        return AgentResult(
            final_text=f"处理失败：{cause}。请稍后重试。\n\n{self._config.disclaimer}",
            stopped_reason="error",
            messages=messages,
            tool_results=[],
            usage=None,
        )

    @staticmethod
    def _log_error(cause: BaseException) -> None:
        _log.error(
            "agent.run failed: %s",
            type(cause).__name__,
            exc_info=(type(cause), cause, cause.__traceback__),
        )

    async def run(
        self,
        user_input: str,
        *,
        history: list[dict[str, Any]] | None = None,
        on_text=None,
        on_thinking=None,
        on_tool=None,
        on_process=None,
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
                config={"configurable": {"on_text": on_text, "on_thinking": on_thinking, "on_tool": on_tool, "on_process": on_process}},
            )
        except asyncio.CancelledError:
            raise  # 客户端断连/取消：透传（BaseException），不误报“处理失败”
        except BaseExceptionGroup as eg:  # 取消组透传，真异常组归一为优雅结果
            leaves = _flatten_exceptions(eg)
            cancel = [e for e in leaves if isinstance(e, asyncio.CancelledError)]
            if cancel:
                raise cancel[0]
            return self._error_result(messages, leaves[-1] if leaves else eg)
        except Exception as exc:  # noqa: BLE001 - 图普通异常归一为优雅结果
            return self._error_result(messages, exc)
        return AgentResult(
            final_text=result.get("final_answer") or "",
            stopped_reason=result.get("stopped_reason") or "end_turn",
            messages=result.get("messages") or messages,
            tool_results=result.get("tool_results") or [],
            usage=result.get("usage"),
            citations=result.get("citations") or [],
            structured=result.get("structured"),
        )
