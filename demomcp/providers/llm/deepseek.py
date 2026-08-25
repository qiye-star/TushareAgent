"""DeepSeek LLM 后端：走 OpenAI 兼容 API（openai SDK + 自定义 base_url）。

把中立 ToolSpec 转成 OpenAI function 工具，把 OpenAI 流式 / 完整响应归一化为中立 ChatResponse，
并把 assistant 轮 / 工具结果消息帧渲染成 OpenAI 格式（role=tool / tool_call_id）。
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from demomcp.interfaces.llm_client import OnText, OnThinking
from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse


def to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema or {"type": "object", "properties": {}},
        },
    }


def map_finish(finish_reason: str | None, has_tool_calls: bool) -> str:
    """OpenAI/DeepSeek 的 finish_reason → 中立的 stop_reason。"""
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    if finish_reason in ("stop", None):
        return "tool_use" if has_tool_calls else "end_turn"
    return finish_reason or "end_turn"


class DeepSeekLLMClient:
    def __init__(self, *, api_key: str, base_url: str | None = None, model: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model or "deepseek-chat"

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        system: str | None = None,
        max_tokens: int = 8192,
        stream: bool = True,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
    ) -> ChatResponse:
        msgs = list(messages)
        if system:
            msgs.insert(0, {"role": "system", "content": system})
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": msgs,
            "max_tokens": max_tokens,
            "tool_choice": "auto",
        }
        if tools:
            kwargs["tools"] = [to_openai_tool(t) for t in tools]

        if stream:
            return await self._chat_stream(kwargs, on_text, on_thinking)
        return await self._chat_once(kwargs)

    async def _chat_stream(
        self,
        kwargs: dict[str, Any],
        on_text: OnText | None,
        on_thinking: OnThinking | None,
    ) -> ChatResponse:
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, Any] | None = None
        tool_acc: dict[int, dict[str, str]] = {}

        stream = await self._client.chat.completions.create(**kwargs, stream=True)
        async for chunk in stream:
            if chunk.usage:
                usage = chunk.usage.model_dump()
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta.content:
                text_parts.append(delta.content)
                if on_text:
                    await on_text(delta.content)
            thinking = getattr(delta, "reasoning_content", None)
            if thinking:
                thinking_parts.append(thinking)
                if on_thinking:
                    await on_thinking(thinking)
            for tc in delta.tool_calls or []:
                acc = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments

        return self._build_response(
            text="".join(text_parts),
            thinking="".join(thinking_parts),
            finish_reason=finish_reason,
            tool_acc=tool_acc,
            usage=usage,
        )

    async def _chat_once(self, kwargs: dict[str, Any]) -> ChatResponse:
        completion = await self._client.chat.completions.create(**kwargs)
        message = completion.choices[0].message
        text = message.content or ""
        tool_uses: list[ToolUse] = []
        raw_tool_calls: list[dict[str, Any]] = []
        for tc in message.tool_calls or []:
            name = tc.function.name
            arguments = tc.function.arguments or "{}"
            tool_uses.append(ToolUse(id=tc.id, name=name, input=json.loads(arguments)))
            raw_tool_calls.append(
                {"id": tc.id, "type": "function", "function": {"name": name, "arguments": arguments}}
            )
        usage = completion.usage.model_dump() if completion.usage else None
        reasoning = getattr(message, "reasoning_content", None)
        raw_content: dict[str, Any] = {
            "content": message.content or None,
            "tool_calls": raw_tool_calls or None,
        }
        if reasoning:
            raw_content["reasoning_content"] = reasoning
        return ChatResponse(
            stop_reason=map_finish(completion.choices[0].finish_reason, bool(tool_uses)),
            tool_uses=tool_uses,
            raw_content=raw_content,
            text=text,
            usage=usage,
        )

    def _build_response(
        self,
        *,
        text: str,
        thinking: str,
        finish_reason: str | None,
        tool_acc: dict[int, dict[str, str]],
        usage: dict[str, Any] | None,
    ) -> ChatResponse:
        ordered = [tool_acc[i] for i in sorted(tool_acc)]
        tool_uses = [
            ToolUse(id=a["id"], name=a["name"], input=json.loads(a["arguments"] or "{}")) for a in ordered
        ]
        raw_tool_calls = [
            {"id": a["id"], "type": "function", "function": {"name": a["name"], "arguments": a["arguments"]}}
            for a in ordered
        ]
        raw_content: dict[str, Any] = {
            "content": text or None,
            "tool_calls": raw_tool_calls or None,
        }
        if thinking:
            raw_content["reasoning_content"] = thinking
        return ChatResponse(
            stop_reason=map_finish(finish_reason, bool(tool_uses)),
            tool_uses=tool_uses,
            raw_content=raw_content,
            text=text,
            thinking=thinking,
            usage=usage,
        )

    def assistant_message(self, resp: ChatResponse) -> dict[str, Any]:
        """渲染成合法的 OpenAI assistant 消息：content + (tool_calls|reasoning_content)。"""
        msg: dict[str, Any] = {"role": "assistant", "content": resp.text or None}
        raw_calls = resp.raw_content.get("tool_calls")
        if resp.tool_uses and raw_calls:
            msg["tool_calls"] = raw_calls
        reasoning = resp.raw_content.get("reasoning_content")
        if reasoning:
            msg["reasoning_content"] = reasoning
        return msg

    def tool_results_messages(self, results: list[tuple[ToolUse, ToolResult]]) -> list[dict[str, Any]]:
        return [
            {"role": "tool", "tool_call_id": tu.id, "content": tr.content} for tu, tr in results
        ]
