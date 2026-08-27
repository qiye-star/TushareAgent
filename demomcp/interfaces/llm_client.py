"""LLM 供给协议：把「一轮 LLM 回复」与「消息帧」都交由实现负责，保持循环 provider 中立。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse

OnText = Callable[[str], Awaitable[None]]
OnThinking = Callable[[str], Awaitable[None]]


@runtime_checkable
class LLMClient(Protocol):
    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        system: str | None = None,
        max_tokens: int,
        stream: bool = True,
        temperature: float | None = None,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
    ) -> ChatResponse:
        """发起一轮对话，返回中立 ChatResponse；stream 时经回调逐步吐出文本 / 思考。

        temperature=None 表示不覆盖（用 provider 默认）；设为 0 让相同输入得到一致决策/输出。
        """
        ...

    def assistant_message(self, resp: ChatResponse) -> dict[str, Any]:
        """把 assistant 轮渲染成能原样回传的 messages 条目（如 OpenAI 的 content + tool_calls）。"""
        ...

    def tool_results_messages(
        self, results: list[tuple[ToolUse, ToolResult]]
    ) -> list[dict[str, Any]]:
        """把工具结果渲染成能喂回 messages 的条目（如 OpenAI 的 role=tool）。"""
        ...
