"""核心编排层：手动 agentic loop —— 只依赖 interfaces 的 ToolProvider 与 LLMClient。

消息帧（assistant 轮 / 工具结果）由 LLMClient 自己渲染，故换后端不改本循环。
错误以 is_error 落进 LLM 上下文，循环永不崩。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from demomcp.config.settings import Settings
from demomcp.interfaces.llm_client import LLMClient
from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import AgentResult, ToolResult, ToolSpec, ToolUse

OnText = Callable[[str], Awaitable[None]]
OnThinking = Callable[[str], Awaitable[None]]
OnTool = Callable[[str, dict[str, Any], ToolResult], Awaitable[None]]


class Agent:
    def __init__(self, *, llm: LLMClient, tools: ToolProvider, config: Settings) -> None:
        self._llm = llm
        self._tools = tools
        self._config = config
        self._tool_defs: list[ToolSpec] | None = None

    async def run(
        self,
        user_input: str,
        *,
        history: list[dict[str, Any]] | None = None,
        on_text: OnText | None = None,
        on_thinking: OnThinking | None = None,
        on_tool: OnTool | None = None,
    ) -> AgentResult:
        messages = list(history or []) + [{"role": "user", "content": user_input}]
        tool_defs = await self._get_tool_defs()
        text_parts: list[str] = []
        results: list[ToolResult] = []
        usage: dict[str, Any] | None = None

        for _ in range(self._config.max_iterations):
            resp = await self._llm.chat(
                messages=messages,
                tools=tool_defs,
                system=self._config.system_prompt,
                max_tokens=self._config.ds_max_tokens,
                stream=self._config.ds_streaming,
                on_text=on_text,
                on_thinking=on_thinking,
            )
            usage = resp.usage
            if resp.text:
                text_parts.append(resp.text)
            # 无条件把这一轮 assistant 消息（含 text / tool_calls）追进 history，
            # 保证 end_turn 收尾时最终回答也进 messages，多轮历史不丢上一轮答复。
            messages.append(self._llm.assistant_message(resp))

            if resp.stop_reason == "end_turn":
                return self._make_result(text_parts, "end_turn", messages, results, usage)
            if resp.stop_reason == "max_tokens":
                return self._make_result(text_parts, "max_tokens", messages, results, usage)
            if resp.stop_reason == "refusal":
                return self._make_result(text_parts, "refusal", messages, results, usage)
            if not resp.tool_uses:
                return self._make_result(text_parts, "no_progress", messages, results, usage)

            pairs: list[tuple[ToolUse, ToolResult]] = []
            for tu in resp.tool_uses:
                tr = await self._safe_call_tool(tu.name, tu.input)
                pairs.append((tu, tr))
                if on_tool:
                    await on_tool(tu.name, tu.input, tr)
            messages.extend(self._llm.tool_results_messages(pairs))
            results.extend(tr for _, tr in pairs)

        return self._make_result(text_parts, "max_iterations", messages, results, usage)

    async def _get_tool_defs(self) -> list[ToolSpec]:
        if self._tool_defs is None:
            self._tool_defs = await self._tools.list_tools()
        return self._tool_defs

    async def _safe_call_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        try:
            return await self._tools.call_tool(name, args)
        except Exception as exc:  # noqa: BLE001 - 最后一道网：任何异常转 is_error，循环永不崩
            return ToolResult(content=f"Error calling {name}: {exc}", is_error=True)

    @staticmethod
    def _make_result(
        text_parts: list[str],
        stopped_reason: str,
        messages: list[dict[str, Any]],
        results: list[ToolResult],
        usage: dict[str, Any] | None,
    ) -> AgentResult:
        final_text = "".join(text_parts)
        return AgentResult(
            final_text=final_text,
            stopped_reason=stopped_reason,
            messages=messages,
            tool_results=results,
            usage=usage,
        )
