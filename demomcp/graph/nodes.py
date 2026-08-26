"""四节点实现：router / tool_rag / synthesizer / fallback。

节点除 state 外还接收 config，回调（on_text/on_thinking/on_tool）经 `config["configurable"]` 注入，
供 LLM 流式与工具轨迹透传；llm/tools/tool_defs/base_system/disclaimer 通过 make_* 工厂闭包注入。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from demomcp.graph.prompts import ROUTER_SYSTEM, select_system, synth_system
from demomcp.graph.state import GraphState
from demomcp.interfaces.types import ToolResult

_VALID_INTENTS = ("market", "report", "compare")
_MAX_EVIDENCE_CHARS = 2000  # 证据摘要长度上限，控制 Synthesis 上下文体积


def _cf(config: RunnableConfig, key: str) -> Any:
    """从 config.configurable 安全取回调；LangGraph 会往 configurable 追加内部键。"""
    return config.get("configurable", {}).get(key)


async def _safe_call_tool(tools: Any, name: str, arguments: dict[str, Any] | None) -> ToolResult:
    try:
        return await tools.call_tool(name, arguments)
    except Exception as exc:  # noqa: BLE001 - 工具层吞掉一切，转 is_error 保图不崩
        return ToolResult(content=f"Error calling {name}: {exc}", is_error=True)


def make_router(llm: Any, *, max_tokens: int):
    """意图识别 + 越界判断：非流式分类，返回 {intent, out_of_scope}。"""

    async def router(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        intent = "market"
        out_of_scope = False
        usage = None
        try:
            resp = await llm.chat(
                messages=state.get("messages") or [],
                tools=[],
                system=ROUTER_SYSTEM,
                max_tokens=max_tokens,
                stream=False,
                on_text=_cf(config, "on_text"),
                on_thinking=_cf(config, "on_thinking"),
            )
            usage = resp.usage
            try:
                body = json.loads((resp.text or "").strip())
                if isinstance(body, dict):
                    if body.get("intent") in _VALID_INTENTS:
                        intent = body["intent"]
                    out_of_scope = bool(body.get("out_of_scope", False))
            except (ValueError, json.JSONDecodeError):
                pass
        except Exception:  # noqa: BLE001 - LLM 调用异常就默认 market 继续，让下游节点各自兜底 → 自愈
            return {"intent": intent, "out_of_scope": out_of_scope, "usage": usage}
        return {"intent": intent, "out_of_scope": out_of_scope, "usage": usage}

    return router


def make_tool_rag(llm: Any, tools: Any, tool_defs: list, *, max_tokens: int, base_system: str):
    """工具执行与检索：一次工具选择 LLM 轮 → 执行（trail 透传）→ 汇总证据；无进展/无证据则置兜底原因。"""

    async def tool_rag(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        on_thinking = _cf(config, "on_thinking")
        on_tool = _cf(config, "on_tool")
        intent = state.get("intent") or "market"

        messages = list(state.get("messages") or [])
        tool_results = list(state.get("tool_results") or [])
        usage = state.get("usage")
        try:
            resp = await llm.chat(
                messages=state.get("messages") or [],
                tools=tool_defs,
                system=select_system(base_system, intent),
                max_tokens=max_tokens,
                stream=True,
                on_text=on_text,
                on_thinking=on_thinking,
            )
        except Exception:  # noqa: BLE001 - LLM 选工具失败 → 就地兜底（node_error → fallback）
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": [],
                "fallback_reason": "node_error",
                "usage": usage,
            }
        usage = resp.usage or usage

        if not resp.tool_uses:
            return {
                "messages": messages + [llm.assistant_message(resp)],
                "tool_results": tool_results,
                "evidence": [],
                "fallback_reason": "no_progress",
                "usage": usage,
            }

        messages.append(llm.assistant_message(resp))
        pairs: list[tuple[Any, ToolResult]] = []
        evidence: list[dict[str, Any]] = []
        for tu in resp.tool_uses:
            tr = await _safe_call_tool(tools, tu.name, tu.input)
            pairs.append((tu, tr))
            tool_results.append(tr)
            if on_tool:
                await on_tool(tu.name, tu.input, tr)
            content = (tr.content or "").strip()
            if not tr.is_error and content and content != "[]":
                evidence.append({"source": tu.name, "content": content[:_MAX_EVIDENCE_CHARS]})
        if pairs:
            messages.extend(llm.tool_results_messages(pairs))

        if not evidence:
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": evidence,
                "fallback_reason": "no_evidence",
                "usage": usage,
            }
        return {
            "messages": messages,
            "tool_results": tool_results,
            "evidence": evidence,
            "fallback_reason": None,
            "usage": usage,
        }

    return tool_rag


def make_synthesizer(llm: Any, *, max_tokens: int, disclaimer: str, base_system: str):
    """投研生成与格式化：依据证据摘要总结，标注来源，并附免责声明。"""

    async def synthesizer(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        on_thinking = _cf(config, "on_thinking")
        # 用证据摘要（非全量 messages）喂给 LLM，控制 context 体积
        evidence_digest = "\n\n".join(
            f"【{e.get('source')}】{e.get('content', '')}" for e in state.get("evidence", [])
        )
        user_msg = state.get("original_query", "")
        citations = sorted({str(e["source"]) for e in state.get("evidence", []) if e.get("source")})
        try:
            resp = await llm.chat(
                messages=[{"role": "user", "content": user_msg + "\n\n可用数据：\n" + evidence_digest}],
                tools=[],
                system=synth_system(base_system),
                max_tokens=max_tokens,
                stream=True,
                on_text=on_text,
                on_thinking=on_thinking,
            )
        except Exception:  # noqa: BLE001 - 生成失败 → 兜底文案（fallback）
            answer = "未能生成投研总结（处理异常），请稍后重试。"
            if disclaimer:
                answer = f"{answer}\n\n{disclaimer}"
                if on_text:
                    await on_text(answer)
            return {
                "final_answer": answer,
                "citations": citations,
                "messages": list(state.get("messages") or []),
                "stopped_reason": "fallback",
                "usage": state.get("usage"),
            }
        answer = (resp.text or "").strip() or "已取到数据，但未能生成总结。"
        if disclaimer and disclaimer not in answer:
            answer = f"{answer}\n\n{disclaimer}"
            if on_text:
                await on_text(f"\n\n{disclaimer}")
        return {
            "final_answer": answer,
            "citations": citations,
            "messages": list(state.get("messages") or []) + [llm.assistant_message(resp)],
            "stopped_reason": "end_turn",
            "usage": resp.usage or state.get("usage"),
        }

    return synthesizer


def make_fallback(*, disclaimer: str):
    """异常与兜底：确定性文案（超范围/无进展/无证据），不调 LLM，便于测试与稳定输出。"""

    async def fallback(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        if state.get("out_of_scope"):
            text = "超出可查询范围：无法为该标的/维度提供可靠数据。请确认标的范围或查询维度，或改用支持的接口。"
        elif state.get("fallback_reason") == "no_progress":
            text = "未能识别出可用的取数工具，建议换个问法，或明确标的与要查的指标。"
        elif state.get("fallback_reason") == "no_evidence":
            text = "未能取到可靠数据（可能无对应数据或接口无权限），请核对标的/范围与权限，或改用其它接口。"
        elif state.get("fallback_reason") == "node_error":
            text = "取数/生成过程中出现异常，请稍后重试或调整问题。"
        else:
            text = "未能完成本次取数，请稍后重试或调整问题。"
        answer = f"{text}\n\n{disclaimer}"
        if on_text:
            await on_text(answer)
        return {"final_answer": answer, "stopped_reason": "fallback"}

    return fallback
