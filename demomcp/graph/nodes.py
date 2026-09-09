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

from demomcp.config.logging import get_logger
from demomcp.graph.prompts import (
    REWRITE_SYSTEM,
    ROUTER_BASE,
    select_system,
    synth_system,
    today_context,
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

_log = get_logger("graph.nodes")

_VALID_INTENTS = ("market", "report", "compare")
_MAX_EVIDENCE_CHARS = 2000  # 证据摘要长度上限，控制 Synthesis 上下文体积
_MAX_SOURCE_RAW = 20000  # 前端来源卡展示用的完整返回上限（只走 structured.sources，不进 LLM 上下文）
_DIRECT_ANSWER_MIN_CHARS = 30  # 零证据停止轮「直接作答」的最小长度：短句（含测试里的「数据已足。」）不足以定稿
# 取数类提示词引导模型在数据取不到时「如实说明」，所以这些措辞出现在零证据停止轮 = 在承认失败而非作答 → 一律拒绝
_DIRECT_ANSWER_REJECT_PHRASES = (
    "我不知道用", "无法识别", "无法找到", "查不到", "没有找到", "找不到",
    "无法回答", "不知道", "无法获取", "无法取到", "取不到", "无权限", "未接入",
    # 2026-09-07 审查：窄词表漏判的变体措辞（「暂时无法查询/权限不足/抱歉」等均不命中旧表），
    # 零证据停止轮的「抱歉」不可能出现在正常作答里（正常作答走合成器，不走此通道）
    "暂时无法", "无法查询", "查询不到", "没能查询", "抱歉", "权限不足", "权限受限", "没有权限",
)


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
    except Exception as exc:  # noqa: BLE001 - 检索失败/retriever None → 空
        _log.debug("rag_retrieve failed: %s: %s", type(exc).__name__, exc)
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
                "content": _truncate_head_tail(chunk.text),
                "cite": cite.model_dump() if hasattr(cite, "model_dump") else cite,
            }
        )
    if funnel:
        funnel["unique_sources"] = len(seen_inline)
        await _emit_process(config, "funnel", funnel)
    return evidence, list(chunks)


def _intent_event(intent: str, skill_id: str | None, out_of_scope: bool, skills: list[Skill] | None) -> dict[str, Any]:
    """router 的 process 事件载荷；带 skill_name 让前端直接显示「命中技能：xxx」（不用再查 /api/skills）。"""
    skill = _resolve_skill(skill_id, skills)
    return {
        "intent": intent,
        "skill": skill_id,
        "skill_name": skill.name if skill else None,
        "out_of_scope": out_of_scope,
        "strategy": _strategy(intent, skill),
    }


def make_router(llm: Any, *, max_tokens: int, skills: list[Skill] | None = None):
    """意图识别 + 越界判断 + 报告 skill 命中：非流式分类，返回 {intent, skill, out_of_scope}。

    `state["forced_skill"]`（前端技能页「快速使用」指定）能解析到已启用技能时：**不把技能清单拼进
    system**（省 ~4KB/次）、只做 intent + 越界判定，skill 直接用指定值。
    """

    async def router(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        intent = "market"
        forced = _resolve_skill(state.get("forced_skill"), skills)
        skill_id: str | None = forced.id if forced else None
        out_of_scope = False
        usage = None
        try:
            resp = await llm.chat(
                messages=state.get("messages") or [],
                tools=[],
                system=f"{ROUTER_BASE if forced else build_router_system(skills)}\n\n{today_context()}",
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
                    if not forced:  # forced 优先：LLM 这轮没看到技能清单，它填的 skill 不可信
                        s = body.get("skill")
                        skill_id = s if isinstance(s, str) and _resolve_skill(s, skills) else None
                    out_of_scope = bool(body.get("out_of_scope", False))
            except (ValueError, json.JSONDecodeError):
                pass
        except Exception as exc:  # noqa: BLE001 - LLM 调用异常就默认 market 继续，让下游节点各自兜底 → 自愈
            _log.warning("router llm.chat failed: %s: %s", type(exc).__name__, exc)
            await _emit_process(config, "intent", _intent_event(intent, skill_id, out_of_scope, skills))
            return {"intent": intent, "skill": skill_id, "out_of_scope": out_of_scope, "usage": usage}
        await _emit_process(config, "intent", _intent_event(intent, skill_id, out_of_scope, skills))
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
                    system=f"{REWRITE_SYSTEM}\n\n{today_context()}",
                    max_tokens=max_tokens,
                    stream=False,
                    temperature=0.0,
                )
                rewritten = (resp.text or "").strip()
            except Exception as exc:  # noqa: BLE001 - LLM 改写失败 → 确定性兜底
                _log.warning("rewrite_query llm.chat failed: %s: %s", type(exc).__name__, exc)
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
            # 本节点只选工具/判断是否收尾，正常路径不产出最终答案（resp.text 不在本函数被读取）——
            # 任何流式 content 都按思考过程处理（on_text 接 on_thinking），不接答案通道；
            # 否则模型在「数据已足够、收尾」那一轮吐出的文字会被当成答案先流一遍，
            # 之后又被 synthesizer 的真实生成顶掉，表现为「答案被第二次生成覆盖」。
            # 唯一例外：下方零证据停止轮的「直接作答」（回显上一轮答案等），经 _accept_as_direct_answer
            # 判定后转正为答案（见 f2d8b91b 线上事故——同题二答回显被当成"没取到数"误导兜底）。
            resp = await llm.chat(
                messages=messages,
                tools=revealed,
                system=select_system(base_system, intent, skill.tool_hint if skill else "", round_no=round_no, max_iterations=max_iterations),
                max_tokens=max_tokens,
                stream=True,
                temperature=0.0,
                on_text=on_thinking,
                on_thinking=on_thinking,
            )
        except Exception as exc:  # noqa: BLE001 - LLM 选工具失败 → 仍先试 RAG；有累积证据则合成，否则 node_error
            _log.warning("tool_rag select-tools llm.chat failed (round=%d): %s: %s", round_no, type(exc).__name__, exc)
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

        _append_assistant_frame(messages, llm, resp)

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
            direct_answer: str | None = None
            if evidence or rag_chunks:
                fallback_reason = None  # 已足够 → 合成器
            else:
                # 零证据停止轮：LLM 可能「直接作答」（最常见：同题二答时回显上一轮答案，见 f2d8b91b），
                # 也可能在承认取不到/不会用工具。前者定稿下发（取代误导性兜底），后者诚实兜底。
                if resp.stop_reason == "end_turn" and _accept_as_direct_answer(resp.text or ""):
                    direct_answer = (resp.text or "").strip()
                    fallback_reason = None  # 直接作答 → 合成器定稿
                else:
                    fallback_reason = "no_progress" if not tool_results else "no_evidence"
            return {
                "messages": messages,
                "tool_results": tool_results,
                "evidence": evidence,
                "rag_chunks": rag_chunks,
                "fallback_reason": fallback_reason,
                "direct_answer": direct_answer,
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
        except Exception as exc:  # noqa: BLE001 - 并行一路（非取消）异常 → 有累积证据则合成，否则 parallel_race；取消仍透传
            _log.warning("tool_rag parallel gather failed (round=%d): %s: %s", round_no, type(exc).__name__, exc)
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
                    entry: dict[str, Any] = {
                        "source_type": "tool",
                        "source": tu.name,
                        "content": _truncate_head_tail(content),
                        # raw 只供前端来源卡渲染表格；_evidence_digest 不读它 → 不占 LLM 上下文。
                        # 语义截断保证是有效 JSON（大表也能渲染成真表格），而非硬切在 JSON 中途。
                        "raw": _truncate_json_raw(content),
                    }
                    params = parsed.get("source", {}).get("params") if isinstance(parsed.get("source"), dict) else None
                    if isinstance(params, dict):
                        entry["params"] = params
                        request_params.update(params)
                    evidence.append(entry)
                else:
                    validation_errors.append(_friendly_signal(parsed) or content)  # 友好校验失败，first-class
            elif content != "[]":
                evidence.append(
                    {
                        "source_type": "tool",
                        "source": tu.name,
                        "content": _truncate_head_tail(content),
                        "raw": _truncate_json_raw(content),
                    }
                )
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


def _truncate_head_tail(content: str, limit: int = _MAX_EVIDENCE_CHARS) -> str:
    """长内容保首尾、中段折叠：daily 类接口按 trade_date 降序，首尾恰是区间两端
    （起止交易日/基期日）。切头会让「全年涨跌幅」类计算缺基期值（线上事故：243 行 index_daily
    截后只剩 2024-12 约 8 行，模型如实报「数据未接入」而无法计算）。摘要仍限 limit，只加一行省略标记。"""
    if len(content) <= limit:
        return content
    half = limit // 2
    return (
        f"{content[:half]}\n\n……中间省略约 {len(content) - limit} 字符（如需完整/尾部数据请缩小查询区间）……\n\n"
        f"{content[-half:]}"
    )


def _data_container(body: Any) -> list | None:
    """从解析后的返回里定位「行列表」容器，供 _truncate_json_raw 按行语义截断。

    Tushare 形态：裸数组、{data:[…]}、{data:{columns,rows}}（列信封）、裸对象；不是行列表（如纯 k/v）
    返回 None——此时无从按行截断，只能回退硬切。
    """
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            return data["rows"]
    return None


def _truncate_json_raw(content: str, limit: int = _MAX_SOURCE_RAW) -> str:
    """raw 只供前端来源卡渲染表格，必须保持**有效 JSON**——不能像 content 那样切头切尾或硬切。

    官方/代理返回很大（daily 一年 243 行 ≈50k 字符）时，`content[:limit]` 恰好截在 JSON 中途，
    JSON.parse 失败 → 前端回退成裸文本（线上坑：表格预览变成一坨裸 JSON）。
    这里把「硬切」改成「语义截断」：超限才解析 → 定位行容器 → 二分出能放进 limit 的**完整行数** n，
    只保留前 n 行、其余丢弃，重排回紧凑 JSON——始终可解析，前端能渲染成真表格（只少了尾部几行）。

    小数据走快路径（len <= limit 原样返回），零解析开销。
    """
    if len(content) <= limit:
        return content
    try:
        body = json.loads(content)
    except (ValueError, TypeError):
        # 本身不是合法 JSON（如权限提示/上游报错纯文本）→ 语义截断无从谈起，维持原硬切
        return content[:limit]
    container = _data_container(body)
    if container is None or not container:
        return content[:limit]

    # 每次探针都用原容器的完整副本重建前缀，避免原地缩短后长度参考错乱
    orig = list(container)  # 浅拷贝指针列表即可；元素（行 dict/list）从不改动

    def probe_size(n: int) -> int:
        container[:] = orig[:n]
        return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")))

    total = len(orig)
    lo, hi, best = 1, total, 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if probe_size(mid) <= limit:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    container[:] = orig[:best]
    if isinstance(body, dict) and isinstance(body.get("row_count"), int):
        body["row_count"] = best  # 行数已截断，同步 row_count，避免表头与数据行不一致
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))



def _append_assistant_frame(messages: list[dict[str, Any]], llm: Any, resp: Any) -> None:
    """追加 assistant 轮消息；content 与 tool_calls 双空时不追加。

    线上事故（2026-09-08）：synthesizer 输出被 max_tokens 截断为空时，`assistant_message` 渲染出
    `{"role": "assistant", "content": None, "reasoning_content": …}`——DeepSeek API 要求 assistant
    消息必须带 content 或 tool_calls，空帧写进历史后同一会话的下一轮直接 400（Invalid assistant
    message），整个会话报废。带 tool_calls 的帧（content 可空）是合法载荷，保留。
    """

    frame = llm.assistant_message(resp)
    if frame.get("content") or frame.get("tool_calls"):
        messages.append(frame)


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

        # —— 直接作答短路：tool_rag 停止轮零证据但 LLM 直接作答（回显上一轮答案等；text 已在选工具
        # 轮经 on_thinking 流过思考区）。不再调 LLM、不接 on_text（避免答案区重复流式；答案区由 done 的
        # structured.answer 填充）；skill.render 也不跑——空证据只会渲染满篇「数据未接入」。 ——
        if state.get("direct_answer") and not evidence and not state.get("rag_chunks"):
            answer = state["direct_answer"].strip()
            if disclaimer and disclaimer not in answer:
                answer = f"{answer}\n\n{disclaimer}"
            return {
                "final_answer": answer,
                "citations": citations,
                "messages": list(state.get("messages") or []),  # 选工具轮已 append assistant 帧，勿再追加
                "stopped_reason": "end_turn",
                "usage": state.get("usage"),
                "structured": _structured(answer, intent, strategy, [], pending, skill_id),
            }

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
        system_prompt = (skill.system_prompt(base_system) if skill else synth_system(base_system, intent=intent)) + "\n\n" + today_context()
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
        except Exception as exc:  # noqa: BLE001 - 生成失败 → 兜底文案（fallback）
            _log.warning("synthesizer llm.chat failed: %s: %s", type(exc).__name__, exc)
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
        answer = (resp.text or "").strip()
        if _is_degenerate_answer(answer) or resp.stop_reason == "max_tokens":
            # 三类失败都不可作为答案下发：拒答/空话（线上曾出现：证据充分却只回"抱歉，我无法完成这个请求。"）；
            # 空文本与截断（2026-09-08 线上事故：v4-flash 的思考也计入 max_tokens，ds_max_tokens=8192
            # 的预算被 reasoning 吃光——实测 reasoning_tokens=8190→content 为空；正文写到一半被截断同理）。
            # 统一重试一次并**加倍预算**（截断原因没变的话同预算必再截断）；
            # 不接 on_text——避免刚才那段文字已经流给前端后，重试的字符又叠上去、越看越乱。
            if not answer or resp.stop_reason == "max_tokens":
                _log.warning(
                    "synthesizer 输出为空/截断（stop=%s, completion_tokens=%s），重试",
                    resp.stop_reason,
                    (resp.usage or {}).get("completion_tokens"),
                )
            try:
                retry_resp = await llm.chat(
                    messages=[{"role": "user", "content": user_msg + "\n\n可用数据：\n" + evidence_digest}],
                    tools=[],
                    system=system_prompt,
                    max_tokens=max(max_tokens * 2, 16384),
                    stream=True,
                    temperature=0.0,
                    on_text=None,
                    on_thinking=None,
                )
                retry_answer = (retry_resp.text or "").strip()
            except Exception as exc:  # noqa: BLE001 - 重试异常按"仍然失败"处理，走下面的诚实兜底
                _log.warning("synthesizer retry llm.chat failed: %s: %s", type(exc).__name__, exc)
                retry_resp, retry_answer = None, ""
            if (
                retry_answer
                and not _is_degenerate_answer(retry_answer)
                and retry_resp is not None
                and retry_resp.stop_reason != "max_tokens"
            ):
                resp, answer = retry_resp, retry_answer
            else:
                # 重试仍失败（拒答/空/再截断）：不再包装成"成功"。不拼万得来源声明——没有真实总结内容时
                # 标来源反而误导，正是这次事故的样子；disclaimer 仍保留（通用声明，不误导）。
                # sources/citations 不清空：证据是真实取到的，取数本身没失败，只是文字总结失败，
                # 用户仍应能在引用来源里核对。
                answer = (
                    f"已取到 {len(evidence)} 条数据（可在下方「查看引用来源」中核对），"
                    "但未能生成可靠的文字总结，请尝试换个问法或缩小问题范围后重新提问。"
                    if evidence
                    else "未能生成可靠的文字总结，请尝试换个问法或缩小问题范围后重新提问。"
                )
                if disclaimer and disclaimer not in answer:
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
        next_messages = list(state.get("messages") or [])
        _append_assistant_frame(next_messages, llm, resp)
        return {
            "final_answer": answer,
            "citations": citations,
            "messages": next_messages,
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
            text = "本轮未调用取数工具且没有获取到可校验的数据，无法给出有依据的回答。建议换个问法，或明确标的与要查的指标。"
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


_REFUSAL_MARKERS = ("抱歉，我无法", "很抱歉，我无法", "对不起，我无法", "我无法完成", "我不能完成", "无法完成这个请求", "我不能提供")


def _is_degenerate_answer(text: str) -> bool:
    """判定这段文本是不是"拒答"而非基于证据的真实总结（如线上曾出现的"抱歉，我无法完成这个请求。"）。

    只认典型的道歉/拒绝措辞 + 篇幅很短，不单纯按长度/是否含数字判断——真实答案有时也很简短
    （比如一句定性结论），单纯"短就当拒答"会误伤这类正常输出。代价是模型换一种说法拒答时会漏判，
    但比误伤正常短答案更安全。
    """
    stripped = text.strip()
    if not stripped:
        return True
    return len(stripped) <= 40 and any(m in stripped for m in _REFUSAL_MARKERS)


def _accept_as_direct_answer(text: str) -> bool:
    """零证据停止轮的「直接作答」判定：非空、非拒答、不含「取不到」类措辞；长度门限对含数字的短句放行。

    判拒答不看长度：合成路径对拒答有重试+诚实兜底，而直接作答路径是原样透传（无重试），
    任何拒答措辞都不应成为 final_answer。长度门限只为挡「好的。」这类无信息短句——
    带数字的短句（如「2024年沪深300涨跌幅为14.68%。」21 字符）是完整答案，不应弹回兜底（2026-09-07 审查）。
    """
    stripped = (text or "").strip()
    if _is_degenerate_answer(stripped):
        return False
    if any(m in stripped for m in _REFUSAL_MARKERS):  # 长度无关的长拒答也硬拒
        return False
    if len(stripped) < _DIRECT_ANSWER_MIN_CHARS and not any(ch.isdigit() for ch in stripped):
        return False
    return not any(p in stripped for p in _DIRECT_ANSWER_REJECT_PHRASES)


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
    except Exception as exc:  # noqa: BLE001 - 渲染器异常 → 兜底文案，保图不崩
        _log.warning("skill.render failed: %s: %s", type(exc).__name__, exc)
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
    """metadata：归一化后的请求参数 + 校验错误（first-class）+ 是否直接作答（取证用）。"""
    req = state.get("request_params") or {}
    return {
        "request": req,
        "validation": {"errors": list(state.get("validation_errors") or []), "normalized": bool(req)},
        "tool_results": len(state.get("tool_results") or []),
        "rag_chunks": len(state.get("rag_chunks") or []),
        "direct_answer": bool(state.get("direct_answer")),
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
            # data 下发完整返回（raw），前端据此解析成表格；截断只保留在喂 LLM 的 content 上
            sources.append({"type": "tool", "title": e.get("source"), "data": e.get("raw") or e.get("content", ""), "params": e.get("params")})
            citations.append({"ref_index": i, "type": "tool", "title": e.get("source")})
        claims.append({"text": f"{e.get('source')} 提供的数据/信息", "source": e.get("source")})
    return {"answer": answer, "intent": intent, "skill": skill, "strategy": strategy, "sources": sources, "citations": citations, "claims": claims, "metadata": metadata}
