"""四节点实现：router / tool_rag / synthesizer / fallback。

节点除 state 外还接收 config，回调（on_text/on_thinking/on_tool）经 `config["configurable"]` 注入，
供 LLM 流式与工具轨迹透传；llm/tools/tool_defs/base_system/disclaimer 通过 make_* 工厂闭包注入。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from demomcp.graph.prompts import (
    REWRITE_SYSTEM,
    select_system,
    synth_system,
)
from demomcp.graph.skills import SKILLS, Skill, build_router_system
from demomcp.graph.state import GraphState
from demomcp.interfaces.types import ToolResult
from demomcp.rag.citing import chunk_to_cite_ref
from demomcp.rag.query_build import (
    DOMAIN_ALIASES,
    expand_keywords,
    extract_concepts,
    infer_filters,
)
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


def _merge_usage(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any] | None:
    """把两轮 LLM 的 token 计数累加（prompt/completion/total）；缺一源则直接用另一个。"""
    if not a:
        return b
    if not b:
        return a
    out = dict(a)
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if isinstance(a.get(k), int) and isinstance(b.get(k), int):
            out[k] = a[k] + b[k]
    return out


def _strategy(intent: str | None, skill: Skill | None = None) -> str:
    """意图 → RAG 检索策略：财报细节用 factual，对比用 structural，行情/未定用 auto；skill 命中时优先用 skill.strategy。"""
    if skill is not None:
        return skill.strategy
    return {"report": "factual", "compare": "structural"}.get(intent or "", "auto")


def _should_rag(intent: str | None, skill: Skill | None = None) -> bool:
    """是否对当前意图跑 RAG：纯行情/指标（market）由工具取数担当，跳过检索省开销降噪；report/compare/未定走 RAG。

    skill 命中时以 skill.should_rag 为准（如快报=行情/资金流/公告/新闻，默认跳过年报 RAG）。
    """
    if skill is not None:
        return skill.should_rag
    return (intent or "market") != "market"


def _resolve_skill(skill_id: str | None, skills: list[Skill] | None) -> Skill | None:
    """在注入的 skill 注册表（skills 为空 → 全局 SKILLS）里按 id 查找，未知/空返回 None。"""
    if not skill_id:
        return None
    registry = skills if skills is not None else SKILLS
    for s in registry:
        if s.id == skill_id:
            return s
    return None


def _parse_tool_result(text: str) -> dict[str, Any] | None:
    """解析语义契约 JSON（含 data/ok）；非契约（如官方 MCP 返回数组）返回 None。"""
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) and ("data" in body or "ok" in body) else None


async def _rag_retrieve(
    retriever: Any, state: GraphState, intent: str | None, skill: Skill | None, config: RunnableConfig
) -> tuple[list[dict[str, Any]], list[Any]]:
    """构造 RetrievalPlan（含公司/年份 filters）→ 检索（重排）→ rag 证据 + RagChunk；总是抛 retrieval 事件。"""
    q = state.get("original_query", "") or ""
    rq = state.get("rewritten_query") or q  # rewrite_query 节点改写后的检索子句（确定性）
    filters = infer_filters(q)
    if filters.company is None:
        # 语料只有比亚迪/宁德时代：解析不到公司（如贵州茅台/指数/泛行业）就跳过 RAG，
        # 避免在无公司过滤时把别家年报切片搜出来污染答案（空 RAG 由工具路兜底）。
        await _emit_process(
            config, "retrieval",
            {"strategy": _strategy(intent, skill), "chunks": 0, "sources": [], "skipped": "no_company"},
        )
        return [], []
    plan = RetrievalPlan(
        rewritten_query=rq,
        concepts=extract_concepts(q),
        filters=filters,
        strategy=_strategy(intent, skill),
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


def make_router(llm: Any, *, max_tokens: int, skills: list[Skill] | None = None):
    """意图识别 + 越界判断 + 报告 skill 命中：非流式分类，返回 {intent, skill, out_of_scope}。"""

    async def router(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        intent = "market"
        skill_id: str | None = None
        out_of_scope = False
        usage = None
        try:
            resp = await llm.chat(
                messages=state.get("messages") or [],
                tools=[],
                system=build_router_system(skills),
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
                    s = body.get("skill")
                    skill_id = s if isinstance(s, str) and _resolve_skill(s, skills) else None
                    out_of_scope = bool(body.get("out_of_scope", False))
            except (ValueError, json.JSONDecodeError):
                pass
        except Exception:  # noqa: BLE001 - LLM 调用异常就默认 market 继续，让下游节点各自兜底 → 自愈
            await _emit_process(config, "intent", {"intent": intent, "skill": skill_id, "out_of_scope": out_of_scope, "strategy": _strategy(intent, _resolve_skill(skill_id, skills))})
            return {"intent": intent, "skill": skill_id, "out_of_scope": out_of_scope, "usage": usage}
        await _emit_process(config, "intent", {"intent": intent, "skill": skill_id, "out_of_scope": out_of_scope, "strategy": _strategy(intent, _resolve_skill(skill_id, skills))})
        return {"intent": intent, "skill": skill_id, "out_of_scope": out_of_scope, "usage": usage}

    return router


# 口语/泛指 → 年报措辞：共享常量在 rag/query_build.DOMAIN_ALIASES（避免与 tool_select 副本漂移）。


def _domain_expand(q: str) -> list[str]:
    """把命中触发词的口语，展开成年报措辞（供检索子句与 BM25 命中相关章节）。"""
    out: list[str] = []
    for trigger, terms in DOMAIN_ALIASES.items():
        if trigger in q:
            for t in terms:
                if t not in out:
                    out.append(t)
    return out


def _deterministic_rewrite(q: str) -> str:
    """确定性改写兜底：原句 + 公司 + 年份 + 财务术语 + 口语→年报措辞（子串感知去重）；异常回退原句。"""
    try:
        f = infer_filters(q)
        kws = expand_keywords(q) or []
        domain = _domain_expand(q)
        parts = [q] if q else []
        for x in (f.company, str(f.year) if f.year else "", *kws, *domain):
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
    llm: Any, tools: Any, tool_defs: list, *, max_tokens: int, base_system: str, retriever: Any | None = None,
    skills: list[Skill] | None = None, curate: Any | None = None, max_iterations: int = 10,
    no_progress_cap: int = 2,
):
    """工具执行与检索（agentic tool loop）：LLM 选工具 → 并行执行 + RAG → 归一化参数/校验 → 汇总证据。

    「循环感知」：本节点可被 tool_rag → tool_rag 条件自环反复进入。每轮从 state 读**已累积**的
    messages/evidence/rag_chunks/tool_results 并回传（LangGraph 用返回值整体替换这些键），因此第 N 轮
    LLM 能「看到」第 1..N-1 轮的工具返回帧来决策。RAG 每轮只第一次检索（`rag_retrieved` 落回 state）。

    退出语义：LLM 返回 tool_uses → 执行并置 `want_more=True`（不到上限则回环）；返回**无** tool_uses
    → 数据已足够，置 `want_more=False`（去合成，若双空则兜底）；LLM 异常/并行竞态 → 有累积证据则合成、无则 fallback。

    `curate(specs, query) -> list[ToolSpec]` 为空或 None 时不做裁剪（全量进 LLM）；非空时只把相关子集
    喂给 LLM（执行仍走全量 `tools.call_tool`，不受限）。
    """

    async def tool_rag(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        on_thinking = _cf(config, "on_thinking")
        on_tool = _cf(config, "on_tool")
        intent = state.get("intent") or "market"
        skill = _resolve_skill(state.get("skill"), skills)

        # 循环状态（读旧值累积，写回让 LangGraph 覆盖 state 键）
        loop_index = int(state.get("loop_index") or 0)
        rag_retrieved = bool(state.get("rag_retrieved"))
        no_progress_count = int(state.get("no_progress_count") or 0)
        round_no = loop_index + 1  # 本轮 1-based 展示轮次（含「停止/收尾」轮）

        messages = list(state.get("messages") or [])
        tool_results = list(state.get("tool_results") or [])
        evidence = list(state.get("evidence") or [])
        rag_chunks = list(state.get("rag_chunks") or [])
        usage = state.get("usage")
        validation_errors = list(state.get("validation_errors") or [])
        request_params = dict(state.get("request_params") or {})
        # 本轮开始前的证据/检索量快照：用于「进展守卫」——本轮两者都无新增 → 视为无进展（防死循环）
        ev_before = len(evidence)
        rag_before = len(rag_chunks)

        await _emit_process(config, "stage", {"stage": "tool_rag", "intent": intent, "round": round_no})
        revealed = (
            curate(
                tool_defs,
                state.get("original_query") or "",
                skill_tools=frozenset(skill.tool_families) if skill else frozenset(),
            )
            if curate
            else tool_defs
        )
        do_rag = (retriever is not None) and _should_rag(intent, skill) and not rag_retrieved  # RAG 每轮只第一次
        try:
            resp = await llm.chat(
                messages=messages,
                tools=revealed,
                system=select_system(base_system, intent, skill.tool_hint if skill else "", round_no=round_no, max_iterations=max_iterations),
                max_tokens=max_tokens,
                stream=True,
                temperature=0.0,
                on_text=on_text,
                on_thinking=on_thinking,
            )
        except Exception:  # noqa: BLE001 - LLM 选工具失败 → 仍先试 RAG；有累积证据则合成，否则 node_error
            rag_evidence, raw_chunks = (await _rag_retrieve(retriever, state, intent, skill, config)) if do_rag else ([], [])
            if rag_evidence:
                evidence.extend(rag_evidence)
            if raw_chunks:
                rag_chunks.extend(raw_chunks)
            await _emit_process(config, "loop_turn", {"round": round_no, "tools": [], "status": "stop", "evidence": len(evidence)})
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": evidence,
                "rag_chunks": rag_chunks,
                "fallback_reason": None if (evidence or rag_chunks) else "node_error",
                "want_more": False,
                "loop_index": loop_index,
                "no_progress_count": 0,
                "rag_retrieved": True,
                "usage": usage,
                "validation_errors": validation_errors,
                "request_params": request_params,
            }
        usage = _merge_usage(usage, resp.usage)

        messages.append(llm.assistant_message(resp))

        # —— 无工具：LLM 判定「数据已足够」（停止轮）。基于**累积**证据判定，不只看本轮。 ——
        if not resp.tool_uses:
            rag_evidence, raw_chunks = (await _rag_retrieve(retriever, state, intent, skill, config)) if do_rag else ([], [])
            if rag_evidence:
                evidence.extend(rag_evidence)
            if raw_chunks:
                rag_chunks.extend(raw_chunks)
            n_tool = sum(1 for e in evidence if e.get("source_type") == "tool")
            await _emit_process(config, "aggregate", {"tool": n_tool, "rag": len(rag_chunks), "total": len(evidence), "round": round_no})
            await _emit_process(config, "loop_turn", {"round": round_no, "tools": [], "status": "stop", "evidence": len(evidence)})
            if evidence or rag_chunks:
                fallback_reason = None  # 已足够 → 合成器
            else:
                fallback_reason = "no_progress" if not tool_results else "no_evidence"
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": evidence,
                "rag_chunks": rag_chunks,
                "fallback_reason": fallback_reason,
                "want_more": False,
                "loop_index": loop_index,
                "no_progress_count": 0,
                "rag_retrieved": True,
                "usage": usage,
                "validation_errors": validation_errors,
                "request_params": request_params,
            }

        # —— 有工具：并行执行（RAG 本轮首次检索 + 全部工具调用），累积证据并回环让 LLM 再评估。 ——
        await _emit_process(config, "plan", {"tools": [tu.name for tu in resp.tool_uses], "round": round_no})
        rag_coro = _rag_retrieve(retriever, state, intent, skill, config) if do_rag else _empty_rag()
        tool_coros = [_safe_call_tool(tools, tu.name, tu.input) for tu in resp.tool_uses]
        try:
            gathered = await asyncio.gather(rag_coro, *tool_coros)
        except Exception:  # noqa: BLE001 - 并行一路（非取消）异常 → 有累积证据则合成，否则 parallel_race；取消仍透传
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": evidence,
                "rag_chunks": rag_chunks,
                "fallback_reason": None if (evidence or rag_chunks) else "parallel_race",
                "want_more": False,
                "loop_index": loop_index,
                "no_progress_count": 0,
                "rag_retrieved": True,
                "usage": usage,
                "validation_errors": validation_errors,
                "request_params": request_params,
            }
        rag_evidence, raw_chunks = gathered[0]
        if rag_evidence:
            evidence.extend(rag_evidence)
        if raw_chunks:
            rag_chunks.extend(raw_chunks)
        tool_pairs = list(zip(resp.tool_uses, gathered[1:]))

        pairs: list[tuple[Any, ToolResult]] = []
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
        await _emit_process(config, "params", {"request": request_params, "round": round_no})
        if validation_errors:
            await _emit_process(config, "validation", {"errors": validation_errors, "round": round_no})

        # 循环状态：进展感知。本轮 evidence/rag 有新增 → 清零计数续转；连续无进展达 no_progress_cap（默认 2）
        # 即终止——防止「无权限/无数据/查不到」时反复空转，烧满 max_iterations 造成死循环；到达上限（max-reached）也终止。
        # 终止且仍有累积证据 → 合成；双空 → no_evidence 兜底（走「未能取到可靠数据」文案）。
        new_index = loop_index + 1
        made_progress = len(evidence) > ev_before or len(rag_chunks) > rag_before
        no_progress = 0 if made_progress else no_progress_count + 1
        at_cap = new_index >= max_iterations
        hit_terminal = at_cap or (not made_progress and no_progress >= no_progress_cap)
        status = "max-reached" if at_cap else ("continue" if made_progress else "no-progress")
        n_tool = sum(1 for e in evidence if e.get("source_type") == "tool")
        await _emit_process(config, "aggregate", {"tool": n_tool, "rag": len(rag_chunks), "total": len(evidence), "round": new_index})
        await _emit_process(config, "loop_turn", {"round": new_index, "tools": [tu.name for tu in resp.tool_uses], "status": status, "evidence": len(evidence)})
        fallback_reason = "no_evidence" if (hit_terminal and not (evidence or rag_chunks)) else None
        return {
            "messages": messages,
            "tool_results": tool_results,
            "evidence": evidence,
            "rag_chunks": rag_chunks,
            "fallback_reason": fallback_reason,
            "want_more": not hit_terminal,
            "loop_index": new_index,
            "no_progress_count": no_progress,
            "rag_retrieved": True,
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


def make_synthesizer(llm: Any, *, max_tokens: int, disclaimer: str, base_system: str,
                     skills: list[Skill] | None = None):
    """投研生成与格式化：依据证据摘要总结，标注来源，并附免责声明；skill 命中时用该 skill 的系统提示词。"""

    async def synthesizer(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        on_text = _cf(config, "on_text")
        on_thinking = _cf(config, "on_thinking")
        await _emit_process(config, "stage", {"stage": "synthesizer"})
        # 用证据摘要（非全量 messages）喂给 LLM，控制 context 体积
        evidence = state.get("evidence") or []
        intent = state.get("intent") or "market"
        skill = _resolve_skill(state.get("skill"), skills)
        skill_id = skill.id if skill else None
        strategy = _strategy(intent, skill)
        evidence_digest = _evidence_digest(evidence)
        user_msg = state.get("original_query", "")
        citations = sorted({str(e.get("source")) for e in evidence if e.get("source")})
        pending = _structured_metadata(state, strategy)

        # —— 纯模板渲染：skill 提供 render 时不调 LLM，直接由代码把 evidence 渲染成六段 ——
        if skill and skill.render:
            answer = _render_answer(skill, state, evidence, user_msg, disclaimer)
            if on_text:
                await on_text(answer)
            return {
                "final_answer": answer,
                "citations": citations,
                "messages": list(state.get("messages") or []) + [{"role": "assistant", "content": answer}],
                "stopped_reason": "end_turn",
                "usage": state.get("usage"),
                "structured": _structured(answer, intent, strategy, evidence, pending, skill_id),
            }

        evidence_digest = _evidence_digest(evidence)
        system_prompt = skill.system_prompt(base_system) if skill else synth_system(base_system, intent=intent)
        try:
            resp = await llm.chat(
                messages=[{"role": "user", "content": user_msg + "\n\n可用数据：\n" + evidence_digest}],
                tools=[],
                system=system_prompt,
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
                "structured": _structured(answer, intent, strategy, evidence, pending, skill_id),
            }
        answer = (resp.text or "").strip() or "已取到数据，但未能生成总结。"
        if disclaimer and disclaimer not in answer:
            answer = f"{answer}\n\n{disclaimer}"
            if on_text:
                await on_text(f"\n\n{disclaimer}")
        if _evidence_uses_wind(evidence):
            attribution = "数据来源于万得 Wind 金融数据服务。"
            if attribution not in answer:
                answer = f"{answer}\n\n{attribution}"
                if on_text:
                    await on_text(f"\n\n{attribution}")
        return {
            "final_answer": answer,
            "citations": citations,
            "messages": list(state.get("messages") or []) + [llm.assistant_message(resp)],
            "stopped_reason": "end_turn",
            "usage": resp.usage or state.get("usage"),
            "structured": _structured(answer, intent, strategy, evidence, pending, skill_id),
        }

    return synthesizer


def make_fallback(*, disclaimer: str, skills: list[Skill] | None = None):
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
        skill = _resolve_skill(state.get("skill"), skills)
        strategy = _strategy(state.get("intent"), skill)
        structured = {
            "answer": answer,
            "intent": state.get("intent"),
            "skill": state.get("skill"),
            "strategy": strategy,
            "sources": [],
            "citations": [],
            "claims": [],
            "metadata": _structured_metadata(state, strategy),
        }
        return {"final_answer": answer, "stopped_reason": "fallback", "structured": structured}

    return fallback


def _evidence_uses_wind(evidence: list[dict[str, Any]]) -> bool:
    """是否存在来自万得（wind_ 前缀工具）的证据 → 用于追加来源声明。"""
    return any(str(e.get("source", "")).startswith("wind_") for e in evidence)


def _render_answer(skill: Skill, state: GraphState, evidence: list[dict[str, Any]], user_msg: str, disclaimer: str) -> str:
    """纯模板渲染：调 skill.render(evidence, ctx) 生成答案，并补免责声明与 Wind 来源声明。"""
    try:
        answer = skill.render(
            evidence,
            {"query": user_msg, "date": datetime.now(UTC).date().isoformat(), "request": state.get("request_params") or {}},
        )
    except Exception:  # noqa: BLE001 - 渲染器异常 → 兜底文案，保图不崩
        answer = "未能生成投研快报（渲染异常），请稍后重试。"
    answer = answer.strip() or "已取到数据，但未能渲染快报。"
    if disclaimer and disclaimer not in answer:
        answer = f"{answer}\n\n{disclaimer}"
    if _evidence_uses_wind(evidence):
        attribution = "数据来源于万得 Wind 金融数据服务。"
        if attribution not in answer:
            answer = f"{answer}\n\n{attribution}"
    return answer


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
    skill: str | None = None,
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
    return {"answer": answer, "intent": intent, "skill": skill, "strategy": strategy, "sources": sources, "citations": citations, "claims": claims, "metadata": metadata}
