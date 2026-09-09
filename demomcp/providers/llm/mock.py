"""脚本化 / 假 LLM：用于离线测试与无 key 演示，可逐轮返回预设的 ChatResponse。"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.llm_client import OnText, OnThinking
from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse


class MockLLM:
    def __init__(self, responses: list[ChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        system: str | None = None,
        max_tokens: int = 8192,
        stream: bool = True,
        temperature: float | None = None,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
    ) -> ChatResponse:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "system": system,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        resp = self._responses.pop(0)
        if on_text and resp.text:
            await on_text(resp.text)
        if on_thinking and resp.thinking:
            await on_thinking(resp.thinking)
        return resp

    def assistant_message(self, resp: ChatResponse) -> dict[str, Any]:
        """渲染 assistant 轮：content 来自 resp.text，tool_calls 来自 raw_content（与 DeepSeek 实现一致）。"""
        msg: dict[str, Any] = {"role": "assistant", "content": resp.text or None}
        raw_calls = resp.raw_content.get("tool_calls")
        if resp.tool_uses and raw_calls:
            msg["tool_calls"] = raw_calls
        return msg

    def tool_results_messages(self, results: list[tuple[ToolUse, ToolResult]]) -> list[dict[str, Any]]:
        return [
            {"role": "tool", "tool_call_id": tu.id, "content": tr.content} for tu, tr in results
        ]
