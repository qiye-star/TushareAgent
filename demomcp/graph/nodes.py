"""四节点实现：router / tool_rag / synthesizer / fallback。

节点除 state 外还接收 config，回调（on_text/on_thinking/on_tool）经 `config["configurable"]` 注入，
供 LLM 流式与工具轨迹透传；llm/tools/tool_defs/base_system/disclaimer 通过 make_* 工厂闭包注入。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from demomcp.graph.prompts import (
    REWRITE_SYSTEM,
    ROUTER_SYSTEM,
    select_system,
    synth_system,
)
from demomcp.graph.state import GraphState
from demomcp.interfaces.types import ToolResult
from demomcp.rag.citing import chunk_to_cite_ref
from demomcp.rag.query_build import expand_keywords, extract_concepts, infer_filters
from demomcp.rag.schemas import RetrievalPlan

_VALID_INTENTS = ("market", "report", "compare")
_MAX_EVIDENCE_CHARS = 2000  # 证据摘要长度上限，控制 Synthesis 上下文体积


def _cf(config: RunnableConfig, key: str) -> Any:
    """从 config.configurable 安全取回调；LangGraph 会往 configurable 追加内部键。"""
    return config.get("configurable", {}).get(key)


async def _emit_process(config: RunnableConfig, kind: str, data: dict[str, Any]) -> None:
    """向前端抛「处理过程」事件（on_process(kind, data)）；无回调则静默。"""
    cb = _cf(config, "on_process")
    if cb:
        await cb(kind, data)


async def _safe_call_tool(tools: Any, name: str, arguments: dict[str, Any] | None) -> ToolResult:
    try:
        return await tools.call_tool(name, arguments)
    except Exception as exc:  # noqa: BLE001 - 工具层吞掉一切，转 is_error 保图不崩
        return ToolResult(content=f"Error calling {name}: {exc}", is_error=True)


async def _empty_rag() -> tuple[list[dict[str, Any]], list[Any]]:
    """retriever 为 None 时的占位协程（RAG 一路空，工具一路独立并行）。"""
    return [], []


def _strategy(intent: str | None) -> str:
    """意图 → RAG 检索策略：财报细节用 factual，对比用 structural，行情/未定用 auto。"""
    return {"report": "factual", "compare": "structural"}.get(intent or "", "auto")


def _should_rag(intent: str | None) -> bool:
    """是否对当前意图跑 RAG：纯行情/指标（market）由工具取数担当，跳过检索省开销降噪；report/compare/未定走 RAG。"""
    return (intent or "market") != "market"


def _parse_tool_result(text: str) -> dict[str, Any] | None:
    """解析语义契约 JSON（含 data/ok）；非契约（如官方 MCP 返回数组）返回 None。"""
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) and ("data" in body or "ok" in body) else None


async def _rag_retrieve(
    retriever: Any, state: GraphState, intent: str | None, config: RunnableConfig
) -> tuple[list[dict[str, Any]], list[Any]]:
    """构造 RetrievalPlan（含公司/年份 filters）→ 检索（重排）→ rag 证据 + RagChunk；总是抛 retrieval 事件。"""
    q = state.get("original_query", "") or ""
    rq = state.get("rewritten_query") or q  # rewrite_query 节点改写后的检索子句（确定性）
    plan = RetrievalPlan(
        rewritten_query=rq,
        concepts=extract_concepts(q),
        filters=infer_filters(q),
        strategy=_strategy(intent),
    )
    funnel: dict[str, Any] = {}
    try:
        chunks = await retriever.retrieve(plan, on_funnel=funnel.update)
    except Exception:  # noqa: BLE001 - 检索失败/retriever None → 空
        chunks = []
    if chunks is None:
        chunks = []
    await _emit_process(
        config, "retrieval",
        {"strategy": plan.strategy, "chunks": len(chunks),
         "sources": [c.doc_title for c in chunks[:5] if c.doc_title]},
    )
    evidence: list[dict[str, Any]] = []
    seen_inline: set[str] = set()
    for i, chunk in enumerate(chunks):
        cite = chunk_to_cite_ref(chunk, ref_index=i)
        if getattr(cite, "inline", None):
            seen_inline.add(cite.inline)
        meta = chunk.metadata or {}
        source = " ".join(str(x) for x in (chunk.doc_title, meta.get("company"), meta.get("year")) if x).strip()
        evidence.append(
            {
                "source_type": "rag",
                "source": source,
                "content": chunk.text[: _MAX_EVIDENCE_CHARS],
                "cite": cite.model_dump() if hasattr(cite, "model_dump") else cite,
            }
        )
    if funnel:
        funnel["unique_sources"] = len(seen_inline)
        await _emit_process(config, "funnel", funnel)
    return evidence, list(chunks)


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
                temperature=0.0,
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
            await _emit_process(config, "intent", {"intent": intent, "out_of_scope": out_of_scope, "strategy": _strategy(intent)})
            return {"intent": intent, "out_of_scope": out_of_scope, "usage": usage}
        await _emit_process(config, "intent", {"intent": intent, "out_of_scope": out_of_scope, "strategy": _strategy(intent)})
        return {"intent": intent, "out_of_scope": out_of_scope, "usage": usage}

    return router


def _deterministic_rewrite(q: str) -> str:
    """确定性改写兜底：原句 + 公司 + 年份 + 财务术语（子串感知去重）；异常回退原句。"""
    try:
        f = infer_filters(q)
        kws = expand_keywords(q) or []
        parts = [q] if q else []
        for x in (f.company, str(f.year) if f.year else "", *kws):
            # 追加仅当整词/子串都未出现在原句与已收片段（避免「研发投入情况」再叠「研发投入」）
            if x and x not in parts and (not q or x not in q) and not any(x in p for p in parts):
                parts.append(x)
        return " ".join(parts).strip() or q
    except Exception:  # noqa: BLE001 - 改写失败回退原句，图永不崩
        return q


def make_rewrite_query(llm: Any | None = None, *, max_tokens: int = 256):
    """查询改写节点：LLM 语义改写（更高质、契今年报措辞），供 dense/BM25 检索用。

    llm 为空或调用失败时回退确定性改写（_deterministic_rewrite），图永不崩；
    stream=False + temperature=0 求稳定；改写结果经 on_process("rewrite") 透传前端。
    """

    async def rewrite_query(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        q = state.get("original_query", "") or ""
        rewritten = ""
        if llm is not None:
            try:
                resp = await llm.chat(
                    messages=[{"role": "user", "content": q}],
                    tools=[],
                    system=REWRITE_SYSTEM,
                    max_tokens=max_tokens,
                    stream=False,
                    temperature=0.0,
                )
                rewritten = (resp.text or "").strip()
            except Exception:  # noqa: BLE001 - LLM 改写失败 → 确定性兜底
                rewritten = ""
        if not rewritten:
            rewritten = _deterministic_rewrite(q)
        await _emit_process(config, "rewrite", {"original": q, "rewritten": rewritten})
        return {"rewritten_query": rewritten}

    return rewrite_query


def make_tool_rag(
    llm: Any, tools: Any, tool_defs: list, *, max_tokens: int, base_system: str, retriever: Any | None = None
):
    """工具执行与检索：一次工具选择 LLM 轮 → 执行（trail 透传）→ 归一化参数/校验 + RAG 检索 → 汇总证据。"""

    async def tool_rag(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        on_thinking = _cf(config, "on_thinking")
        on_tool = _cf(config, "on_tool")
        intent = state.get("intent") or "market"

        messages = list(state.get("messages") or [])
        tool_results = list(state.get("tool_results") or [])
        usage = state.get("usage")
        validation_errors = list(state.get("validation_errors") or [])
        request_params = dict(state.get("request_params") or {})
        await _emit_process(config, "stage", {"stage": "tool_rag", "intent": intent})
        try:
            resp = await llm.chat(
                messages=state.get("messages") or [],
                tools=tool_defs,
                system=select_system(base_system, intent),
                max_tokens=max_tokens,
                stream=True,
                temperature=0.0,
                on_text=on_text,
                on_thinking=on_thinking,
            )
        except Exception:  # noqa: BLE001 - LLM 选工具失败 → 仍先试 RAG；两路都失败才 node_error
            rag_evidence, rag_chunks = (await _rag_retrieve(retriever, state, intent, config)) if (retriever is not None and _should_rag(intent)) else ([], [])
            if rag_evidence or rag_chunks:
                return {
                    "messages": messages,
                    "tool_results": tool_results,
                    "evidence": rag_evidence,
                    "rag_chunks": list(rag_chunks),
                    "fallback_reason": None,
                    "usage": usage,
                    "validation_errors": validation_errors,
                    "request_params": request_params,
                }
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": [],
                "rag_chunks": list(rag_chunks),
                "fallback_reason": "node_error",
                "usage": usage,
                "validation_errors": validation_errors,
                "request_params": request_params,
            }
        usage = resp.usage or usage

        messages.append(llm.assistant_message(resp))

        # —— 无工具：仅 RAG 一路证据；仍空 → no_progress ——
        if not resp.tool_uses:
            rag_evidence, rag_chunks = (await _rag_retrieve(retriever, state, intent, config)) if (retriever is not None and _should_rag(intent)) else ([], [])
            if rag_evidence or rag_chunks:
                return {
                    "messages": messages,
                    "tool_results": tool_results,
                    "evidence": rag_evidence,
                    "rag_chunks": list(rag_chunks),
                    "fallback_reason": None,
                    "usage": usage,
                    "validation_errors": validation_errors,
                    "request_params": request_params,
                }
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": [],
                "rag_chunks": [],
                "fallback_reason": "no_progress",
                "usage": usage,
                "validation_errors": validation_errors,
                "request_params": request_params,
            }

        # —— 并行：RAG 检索/重排 与 所有工具调用 同时进行 ——
        await _emit_process(config, "plan", {"tools": [tu.name for tu in resp.tool_uses]})
        rag_coro = _rag_retrieve(retriever, state, intent, config) if (retriever is not None and _should_rag(intent)) else _empty_rag()
        tool_coros = [_safe_call_tool(tools, tu.name, tu.input) for tu in resp.tool_uses]
        gathered = await asyncio.gather(rag_coro, *tool_coros)
        rag_evidence, raw_chunks = gathered[0]
        rag_chunks: list[Any] = list(state.get("rag_chunks") or [])
        if raw_chunks:
            rag_chunks.extend(raw_chunks)
        tool_pairs = list(zip(resp.tool_uses, gathered[1:]))

        pairs: list[tuple[Any, ToolResult]] = []
        evidence: list[dict[str, Any]] = []
        for tu, tr in tool_pairs:
            pairs.append((tu, tr))
            tool_results.append(tr)
            if on_tool:
                await on_tool(tu.name, tu.input, tr)
            content = (tr.content or "").strip()
            if tr.is_error or not content:
                continue  # 错误/空 → 不算证据（不抛异常、可被 RAG 兜底）
            parsed = _parse_tool_result(content)
            if parsed is not None:
                if _is_usable_data(parsed):
                    entry: dict[str, Any] = {"source_type": "tool", "source": tu.name, "content": content[:_MAX_EVIDENCE_CHARS]}
                    params = parsed.get("source", {}).get("params") if isinstance(parsed.get("source"), dict) else None
                    if isinstance(params, dict):
                        entry["params"] = params
                        request_params.update(params)
                    evidence.append(entry)
                else:
                    validation_errors.append(_friendly_signal(parsed) or content)  # 友好校验失败，first-class
            elif content != "[]":
                evidence.append({"source_type": "tool", "source": tu.name, "content": content[:_MAX_EVIDENCE_CHARS]})
        if pairs:
            messages.extend(llm.tool_results_messages(pairs))
        await _emit_process(config, "params", {"request": request_params})
        if validation_errors:
            await _emit_process(config, "validation", {"errors": validation_errors})

        # —— 汇总（审核前）：合并工具证据 + RAG 证据，抛 aggregate 事件供前端观察 ——
        if rag_evidence:
            evidence.extend(rag_evidence)
        n_tool = sum(1 for e in evidence if e.get("source_type") == "tool")
        await _emit_process(config, "aggregate", {"tool": n_tool, "rag": len(rag_chunks), "total": len(evidence)})

        # 只有「工具证据 + RAG 证据」都空才兜底
        if not evidence and not rag_chunks:
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": [],
                "rag_chunks": rag_chunks,
                "fallback_reason": "no_evidence",
                "usage": usage,
                "validation_errors": validation_errors,
                "request_params": request_params,
            }
        return {
            "messages": messages,
            "tool_results": tool_results,
            "evidence": evidence,
            "rag_chunks": rag_chunks,
            "fallback_reason": None,
            "usage": usage,
            "validation_errors": validation_errors,
            "request_params": request_params,
        }

    return tool_rag


def _is_usable_data(parsed: dict[str, Any]) -> bool:
    """语义契约是否给出可用数据：ok 不为 False 且 data 非空。"""
    ok = parsed.get("ok")
    if ok is False:
        return False
    data = parsed.get("data")
    return bool(data)


def _friendly_signal(parsed: dict[str, Any]) -> str | None:
    """从契约/原始文本取友好提示（校验失败信息）。"""
    msg = parsed.get("msg")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return None


def make_synthesizer(llm: Any, *, max_tokens: int, disclaimer: str, base_system: str):
    """投研生成与格式化：依据证据摘要总结，标注来源，并附免责声明。"""

    async def synthesizer(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        on_thinking = _cf(config, "on_thinking")
        await _emit_process(config, "stage", {"stage": "synthesizer"})
        # 用证据摘要（非全量 messages）喂给 LLM，控制 context 体积
        evidence = state.get("evidence") or []
        intent = state.get("intent") or "market"
        strategy = _strategy(intent)
        evidence_digest = _evidence_digest(evidence)
        user_msg = state.get("original_query", "")
        citations = sorted({str(e.get("source")) for e in evidence if e.get("source")})
        pending = _structured_metadata(state, strategy)
        try:
            resp = await llm.chat(
                messages=[{"role": "user", "content": user_msg + "\n\n可用数据：\n" + evidence_digest}],
                tools=[],
                system=synth_system(base_system, intent=intent),
                max_tokens=max_tokens,
                stream=True,
                temperature=0.0,
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
                "structured": _structured(answer, intent, strategy, evidence, pending),
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
            "structured": _structured(answer, intent, strategy, evidence, pending),
        }

    return synthesizer


def make_fallback(*, disclaimer: str):
    """异常与兜底：确定性文案（超范围/无进展/无证据），不调 LLM，便于测试与稳定输出。"""

    async def fallback(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        await _emit_process(config, "stage", {"stage": "fallback", "reason": state.get("fallback_reason")})
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
        errs = state.get("validation_errors") or []
        if errs:
            text = f"{text}\n参数校验未通过：{'；'.join(errs[:3])}"
        answer = f"{text}\n\n{disclaimer}"
        if on_text:
            await on_text(answer)
        strategy = _strategy(state.get("intent"))
        structured = {
            "answer": answer,
            "intent": state.get("intent"),
            "strategy": strategy,
            "sources": [],
            "citations": [],
            "claims": [],
            "metadata": _structured_metadata(state, strategy),
        }
        return {"final_answer": answer, "stopped_reason": "fallback", "structured": structured}

    return fallback


def _evidence_digest(evidence: list[dict[str, Any]]) -> str:
    """上下文工程：rag 带内联引用、tool 带归一化入参，控制体积但保留溯源线索。"""
    parts: list[str] = []
    for i, e in enumerate(evidence, start=1):
        etype = e.get("source_type")
        if etype == "rag" and e.get("cite"):
            marker = e["cite"].get("inline") or e.get("source")
            parts.append(f"[{i}] 【{marker}】{e.get('content', '')}")
        else:
            tag = f"【{e.get('source')}】"
            if e.get("params"):
                tag += f" 请求参数={e['params']}"
            parts.append(f"[{i}] {tag}{e.get('content', '')}")
    return "\n\n".join(parts)


def _structured_metadata(state: GraphState, strategy: str) -> dict[str, Any]:
    """metadata：归一化后的请求参数 + 校验错误（first-class）。"""
    req = state.get("request_params") or {}
    return {
        "request": req,
        "validation": {"errors": list(state.get("validation_errors") or []), "normalized": bool(req)},
        "tool_results": len(state.get("tool_results") or []),
        "rag_chunks": len(state.get("rag_chunks") or []),
    }


def _structured(
    answer: str,
    intent: str | None,
    strategy: str,
    evidence: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """结构化输出：answer + sources（含归一化参数/溯源 + 每条证据内容摘要）+ citations + claims + metadata。

    sources/citations 每条证据各一条（不按来源去重），编号与正文 [n] 对齐，避免「引用来源缺第 N 条」。
    """
    sources: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    for i, e in enumerate(evidence, start=1):
        if e.get("source_type") == "rag":
            cite = e.get("cite") or {}
            sources.append(
                {
                    "type": "rag", "title": cite.get("title", ""), "company": cite.get("company"),
                    "year": cite.get("year"), "page": cite.get("page"), "section": cite.get("section", ""),
                    "inline": cite.get("inline", ""),
                    "excerpt": e.get("content", "")[:200],
                }
            )
            citations.append(
                {"ref_index": i, "type": "rag", "title": cite.get("title", ""), "page": cite.get("page"),
                 "section": cite.get("section", ""), "company": cite.get("company"), "year": cite.get("year"),
                 "inline": cite.get("inline", "")}
            )
        else:
            sources.append({"type": "tool", "title": e.get("source"), "data": e.get("content", "")[:200], "params": e.get("params")})
            citations.append({"ref_index": i, "type": "tool", "title": e.get("source")})
        claims.append({"text": f"{e.get('source')} 提供的数据/信息", "source": e.get("source")})
    return {"answer": answer, "intent": intent, "strategy": strategy, "sources": sources, "citations": citations, "claims": claims, "metadata": metadata}
