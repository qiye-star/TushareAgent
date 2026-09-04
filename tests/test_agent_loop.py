"""核心层离线 gate：用 FakeToolProvider + MockLLM 驱动 LangGraph 四节点 Agent（无网络、无代理、无真 LLM）。

覆盖：意图路由→取数→整合生成(引用+免责声明) 全链路；越界兜底；无进展兜底；无证据兜底；工具异常转 is_error。
"""

from __future__ import annotations

from demomcp.agents.agent import Agent
from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse
from demomcp.providers.llm.mock import MockLLM
from demomcp.providers.tools.fake import FakeToolProvider

SPECS = [
    ToolSpec(name="stock_available", description="列出可用标的", input_schema={"type": "object"}),
    ToolSpec(name="stock_realtime_quote", description="实时行情", input_schema={"type": "object"}),
    ToolSpec(name="stock_price_range", description="区间价格", input_schema={"type": "object"}),
    ToolSpec(name="stock_financials", description="财报", input_schema={"type": "object"}),
]


def _router_response(text: str) -> ChatResponse:
    """router 输出：一段 JSON（意图 + 越界）。"""
    return ChatResponse(stop_reason="end_turn", text=text)


def _tool_use_response(call_id: str, name: str, args: dict) -> ChatResponse:
    """tool_rag 的取数选择轮：返回一次工具调用。"""
    return ChatResponse(
        stop_reason="tool_use",
        tool_uses=[ToolUse(id=call_id, name=name, input=args)],
        raw_content={
            "content": None,
            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": str(args).replace("'", '"')}}],
        },
    )


def _stop_response(text: str = "数据已足。") -> ChatResponse:
    """模拟 LLM 在看过一轮返回后判定「数据已足够」的停止轮（无 tool_calls → 去合成器）。"""
    return ChatResponse(stop_reason="end_turn", text=text)


async def test_router_tool_synthesize_end_to_end(make_settings) -> None:
    tools = FakeToolProvider(
        SPECS,
        {"stock_price_range": ToolResult(content='[{"close": 1604.9}]', is_error=False)},
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 最近价格 区间"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
            _stop_response(),  # 看到返回后判定数据已足 → 停止取数
            ChatResponse(stop_reason="end_turn", text="比亚迪区间约 1604.9 元。"),
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪最近价格")

    assert result.stopped_reason == "end_turn"
    assert "1604" in result.final_text
    assert "不构成投资建议" in result.final_text  # 免责声明
    assert [name for name, _ in tools.calls] == ["stock_price_range"]
    assert len(result.tool_results) == 1
    assert result.tool_results[0].is_error is False
    assert result.citations == ["stock_price_range"]
    assert len(mock.calls) == 5  # router / rewrite_query / tool选择 / 停止判定 / synthesizer
    # 决策与生成步骤都应确定性（temperature=0），同问应得同答
    assert all(c.get("temperature") == 0 for c in mock.calls)


async def test_out_of_scope_fallback(make_settings) -> None:
    mock = MockLLM([_router_response('{"intent":"report","out_of_scope":true}')])
    agent = Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("腾讯股价")

    assert result.stopped_reason == "fallback"
    assert "超出可查询范围" in result.final_text
    assert "不构成投资建议" in result.final_text
    assert result.citations == []
    assert result.tool_results == []
    assert len(mock.calls) == 1  # 只走了 router


async def test_no_tool_call_fallback(make_settings) -> None:
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 行情"),  # rewrite_query
            ChatResponse(stop_reason="end_turn", text="我不知道要用什么工具。"),
        ]
    )
    agent = Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("比亚迪")

    assert result.stopped_reason == "fallback"
    assert "未能识别出可用的取数工具" in result.final_text
    assert result.tool_results == []
    assert len(mock.calls) == 3  # router + rewrite_query + tool选择


async def test_empty_evidence_fallback(make_settings) -> None:
    tools = FakeToolProvider(
        SPECS, {"stock_price_range": ToolResult(content="[]", is_error=False)}
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 最近价格"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
            _stop_response(),  # 看到空结果后判定停止 → 仍无证据 → no_evidence 兜底
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪最近价格")

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    # "[]" 不是错误，但为空数据 → 不算证据
    assert result.tool_results[0].is_error is False
    assert result.citations == []
    assert len(mock.calls) == 4  # router + rewrite_query + tool选择 + 停止判定


async def test_tool_error_becomes_fallback(make_settings) -> None:
    tools = FakeToolProvider(SPECS, {}, raise_on={"stock_price_range"})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 行情"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
            _stop_response(),  # 看到失败结果后判定停止 → 仍无证据 → no_evidence 兜底
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪")

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert len(result.tool_results) == 1
    assert result.tool_results[0].is_error is True  # 异常 → is_error，不算证据
    assert len(mock.calls) == 4  # router + rewrite_query + tool选择 + 停止判定


class _RaisingLLM:
    """LLM 逐次调用都抛异常（模拟瞬时网络/流失败）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls += 1
        raise ConnectionError("boom")

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text or None}

    def tool_results_messages(self, results):
        return []


class _LLMRaisesOnToolFrames:
    """chat 正常返回，但渲染工具结果帧时抛错（模拟节点内非 LLM 异常 → 触发 Agent.run 兜底）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls += 1
        if self.calls == 1:  # router
            return ChatResponse(stop_reason="end_turn", text='{"intent":"market","out_of_scope":false}')
        return _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"})  # tool_rag 选工具

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text or None}

    def tool_results_messages(self, results):
        raise RuntimeError("frame boom")


async def test_llm_error_routes_to_fallback(make_settings) -> None:
    """节点内 llm.chat 抛错 → 就地兜底 → fallback，不抛 ExceptionGroup。"""
    llm = _RaisingLLM()
    agent = Agent(llm=llm, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("比亚迪最近价格")  # 不应抛异常

    assert result.stopped_reason == "fallback"
    assert "不构成投资建议" in result.final_text
    assert llm.calls >= 2  # router 默认 market 继续 → tool_rag 兜底 node_error → fallback


async def test_agent_run_guard_catches_node_error(make_settings) -> None:
    """节点内非 LLM 异常（渲染 tool 帧抛错）→ TaskGroup/ExceptionGroup → Agent.run 兜底转 error，不外泄。"""
    llm = _LLMRaisesOnToolFrames()
    agent = Agent(llm=llm, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("比亚迪")  # 不应抛异常

    assert result.stopped_reason == "error"
    assert "不构成投资建议" in result.final_text


def _fake_retriever(results: list[dict]):
    """duck-type retriever（对齐 RagRetriever.retrieve(plan)），记录调用次数。"""

    class _Fake:
        def __init__(self) -> None:
            self.calls = 0
            self.last_plan = None

        async def retrieve(self, plan, *, on_funnel=None):
            self.calls += 1
            self.last_plan = plan
            if on_funnel is not None:
                on_funnel({"recall_raw": 1, "fused": 1, "sections": 1, "pool": 1, "reranked": 1, "final": 1, "rerank_degraded": False})
            from demomcp.rag.schemas import RagChunk

            return [RagChunk(**r) for r in results]

    return _Fake()


async def test_rag_retrieval_adds_evidence(make_settings) -> None:
    """retriever 返回 RagChunk → 进入 synthesizer，structured.sources/citations 含 rag 溯源。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2024 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 数据已足够 → 去合成
            ChatResponse(stop_reason="end_turn", text="研发投入见比亚迪2024年报。"),
        ]
    )
    retriever = _fake_retriever(
        [{
            "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
        }]
    )
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪研发投入"}], "original_query": "比亚迪研发投入", "out_of_scope": False,
             "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"
    assert retriever.calls == 1
    structured = out["structured"]
    assert structured is not None
    rag_sources = [s for s in structured["sources"] if s["type"] == "rag"]
    assert rag_sources and "比亚迪2024年报" in rag_sources[0]["title"]
    assert rag_sources[0]["page"] == "42"
    rag_cites = [c for c in structured["citations"] if c["type"] == "rag"]
    assert rag_cites and rag_cites[0]["inline"]  # 非空内联标记


async def test_rag_retrieval_no_tool_use(make_settings) -> None:
    """纯知识问题（LLM 不选任何工具）也应走 RAG 检索 → 进入 synthesizer。

    回归：此前 tool_rag 在「无工具」时提前以 no_progress 返回，RAG 分支根本跑不到
    （retriever.calls==0、无 rag 证据、stopped_reason=fallback）——即「智能体接入 RAG 无法命中」。
    """
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            ChatResponse(stop_reason="end_turn", text="研发投入情况如下："),  # 无 tool_uses
            ChatResponse(stop_reason="end_turn", text="比亚迪2025年研发投入。"),
        ]
    )
    retriever = _fake_retriever(
        [{
            "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
        }]
    )
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"  # 不是 fallback（修复前是 no_progress→fallback）
    assert retriever.calls == 1  # RAG 确实被检索（修复前 calls==0）
    structured = out["structured"]
    rag_sources = [s for s in structured["sources"] if s["type"] == "rag"]
    assert rag_sources and "比亚迪2024年报" in rag_sources[0]["title"]
    assert rag_sources[0]["page"] == "42"
    rag_cites = [c for c in structured["citations"] if c["type"] == "rag"]
    assert rag_cites and rag_cites[0]["inline"]


def _rag_chunk() -> dict:
    return {"doc_id": "d1", "doc_title": "比亚迪2025年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2025, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2025年报"}}


async def test_tool_fail_rag_succeeds(make_settings) -> None:
    """工具抛异常(is_error)→非证据、不崩；RAG 命中 → 靠 RAG 兜底走合成器（req#1：工具失败不直接返回）。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {}, raise_on={"stock_price_range"})  # 该工具抛异常 → is_error
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 工具失败但有 RAG 兜底 → 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="据年报，研发投入约X亿元。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"  # 工具失败不崩、不直接返回
    assert retriever.calls == 1
    st = out["structured"]
    rag = [s for s in st["sources"] if s["type"] == "rag"]
    assert rag and rag[0]["title"] == "比亚迪2025年报"  # 靠 RAG 证据兜底


async def test_tool_and_rag_parallel_merge(make_settings) -> None:
    """工具成功 + RAG 命中 → structured.sources 同时含 tool 与 rag（两路并行→汇总，req#2）。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 工具成功 + RAG 命中 → 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="价格与研发信息如下。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    st = out["structured"]
    assert any(s["type"] == "tool" for s in st["sources"])
    assert any(s["type"] == "rag" for s in st["sources"])
    assert retriever.calls == 1


class _SelectRaiseMock:
    """router/合成器（空 tools）正常；选工具轮（tools 非空）抛异常（模拟 LLM 选工具失败）。"""

    def __init__(self, responses) -> None:
        self._empty = list(responses)
        self._i = 0
        self.calls = []

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls.append(bool(tools))
        if tools:
            raise RuntimeError("select boom")
        resp = self._empty[self._i]
        self._i += 1
        return resp

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text}

    def tool_results_messages(self, results):
        return [{"role": "tool", "tool_call_id": tu.id, "content": tr.content} for tu, tr in results]


async def test_tool_select_error_uses_rag(make_settings) -> None:
    """选工具 LLM 抛异常 → 仍先试 RAG；RAG 命中 → 进合成器（不是 node_error/直接返回）。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {})
    mock = _SelectRaiseMock(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2025 研发投入"),  # rewrite_query
            ChatResponse(stop_reason="end_turn", text="据年报，研发投入约X亿元。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪2025年研发投入"}], "original_query": "比亚迪2025年研发投入",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"  # 选工具失败被 RAG 兜底，未 node_error
    assert retriever.calls == 1
    rag = [s for s in out["structured"]["sources"] if s["type"] == "rag"]
    assert rag


def test_structured_one_card_per_evidence() -> None:
    """每条证据各出一条 source/citation（不按来源去重），编号与正文 [n] 对齐；rag 卡带内容摘要区分重复来源。"""
    from demomcp.graph.nodes import _structured

    ev = [
        {"source_type": "rag", "source": "比亚迪 2025", "content": "A",
         "cite": {"inline": "[比亚迪2025年报 - 第42页 3.2 研发投入]", "title": "比亚迪2025年报",
                  "company": "比亚迪", "year": 2025, "page": "42", "section": "3.2"}},
        {"source_type": "rag", "source": "比亚迪 2025", "content": "B",
         "cite": {"inline": "[比亚迪2025年报 - 第42页 3.2 研发投入]", "title": "比亚迪2025年报",
                  "company": "比亚迪", "year": 2025, "page": "42", "section": "3.2"}},
        {"source_type": "tool", "source": "stock_price_range", "content": "[]"},
    ]
    st = _structured("答案", "report", "factual", ev, {})
    rag_sources = [s for s in st["sources"] if s["type"] == "rag"]
    rag_cites = [c for c in st["citations"] if c["type"] == "rag"]
    assert len(rag_sources) == 2  # 不按来源去重：两条同来源证据各出一张卡
    assert len(rag_cites) == 2
    assert rag_sources[0]["excerpt"] == "A"
    assert rag_sources[1]["excerpt"] == "B"  # 内容摘要区分同来源重复块
    assert [c["ref_index"] for c in rag_cites] == [1, 2]  # 1 基，与正文 [n] 对齐
    assert len([s for s in st["sources"] if s["type"] == "tool"]) == 1
    assert len(st["claims"]) == 3  # claims 逐证据


async def test_structured_output_present(make_settings) -> None:
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 行情"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="比亚迪区间约 1604.9 元。"),
        ]
    )
    result = await Agent(llm=mock, tools=tools, config=make_settings()).run("比亚迪")
    assert result.structured is not None
    assert {"answer", "sources", "citations", "claims", "metadata"} <= set(result.structured)
    assert result.structured["answer"]
    assert result.structured["metadata"]["tool_results"] == 1
    assert result.structured["sources"][0]["type"] == "tool"


async def test_params_normalized_and_validation(make_settings) -> None:
    # A) 契约 JSON 带 source.params → 归一化参数进 structured.metadata.request
    tools = FakeToolProvider(
        SPECS,
        {"stock_price_range": ToolResult(content='{"ok":true,"data":{"return_pct":2.65},"source":{"params":{"ts_code":"002594.SZ","start_date":"20260601","end_date":"20260731"}}}', is_error=False)},
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 涨幅"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="涨幅 2.65%。"),
        ]
    )
    r = await Agent(llm=mock, tools=tools, config=make_settings()).run("比亚迪")
    req = r.structured["metadata"]["request"]
    assert req.get("ts_code") == "002594.SZ"
    assert req.get("start_date") == "20260601"
    assert r.structured["metadata"]["validation"]["normalized"] is True

    # B) 友好校验失败（ok=false、无 data）→ 记入 validation.errors、不入证据 → fallback，且文本/结构化可见
    tools2 = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='{"ok":false,"msg":"无法解析日期：近3月"}', is_error=False)})
    mock2 = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 近3月 涨幅"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪", "start": "近3月"}),
            _stop_response(),  # 仍有校验失败、无证据 → 停止 → no_evidence 兜底
        ]
    )
    r2 = await Agent(llm=mock2, tools=tools2, config=make_settings()).run("比亚迪")
    assert r2.stopped_reason == "fallback"
    st2 = r2.structured
    assert st2 is not None
    assert any("无法解析日期" in e for e in st2["metadata"]["validation"]["errors"])
    assert "无法解析日期" in r2.final_text  # 校验问题进正文


async def test_strategy_mapping(make_settings) -> None:
    from demomcp.graph.nodes import _strategy

    assert _strategy("report") == "factual"
    assert _strategy("compare") == "structural"
    assert _strategy("market") == "auto"
    assert _strategy(None) == "auto"


def test_infer_filters() -> None:
    from demomcp.rag.query_build import infer_filters

    f1 = infer_filters("比亚迪2025年研发投入")
    assert f1.company == "比亚迪" and f1.year == 2025
    f2 = infer_filters("宁德时代2024年报毛利率")
    assert f2.company == "宁德时代" and f2.year == 2024
    f3 = infer_filters("最近涨幅怎么样")
    assert f3.company is None and f3.year is None


async def test_on_process_events(make_settings) -> None:
    """节点经 on_process 抛处理过程事件：intent/plan/stage 依序可用。"""
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "比亚迪"}),
            _stop_response(),  # 数据已足 → 去合成
            ChatResponse(stop_reason="end_turn", text="研发投入见年报。"),
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    await Agent(llm=mock, tools=tools, config=make_settings()).run("比亚迪研发投入", on_process=on_process)

    kinds = [k for k, _ in events]
    assert kinds[0] == "intent"           # 意图最先
    assert "intent" in kinds and "plan" in kinds and "stage" in kinds
    intent_ev = next(d for k, d in events if k == "intent")
    assert intent_ev["intent"] == "report"
    assert intent_ev["strategy"] == "factual"


async def test_rag_funnel_process_event(make_settings) -> None:
    """retriever.retrieve 暴露漏斗计数 → 节点 emit on_process("funnel", counts) 供前端观察。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {})
    # 无工具分支（纯知识问题），仅 RAG 一路证据；retriever 会调 on_funnel
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 2024 研发投入"),  # rewrite_query
            ChatResponse(stop_reason="end_turn", text="研发投入见年报。"),  # 无 tool_uses → 仅 RAG
            ChatResponse(stop_reason="end_turn", text="比亚迪2024年研发投入。"),  # synthesizer
        ]
    )
    retriever = _fake_retriever([{
        "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
        "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                     "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
    }])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    state = {"messages": [{"role": "user", "content": "比亚迪研发投入"}], "original_query": "比亚迪研发投入", "out_of_scope": False,
             "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {"on_process": on_process}})

    assert out["stopped_reason"] == "end_turn"
    assert retriever.calls == 1
    funnel_ev = next((d for k, d in events if k == "funnel"), None)
    assert funnel_ev is not None
    assert set(funnel_ev) == {"recall_raw", "fused", "sections", "pool", "reranked", "final", "rerank_degraded", "unique_sources"}


async def test_rewrite_query_composes_query() -> None:
    """rewrite_query 节点确定性产出检索子句：原句 + 公司 + 年份 + 扩词，去重、异常回退。"""
    from demomcp.graph.nodes import make_rewrite_query

    node = make_rewrite_query()
    out = await node({"original_query": "比亚迪2024研发投入", "out_of_scope": False}, {})
    rq = out["rewritten_query"]
    assert rq.startswith("比亚迪2024研发投入")
    assert "比亚迪" in rq
    assert "2024" in rq
    # dedup：重复片段不叠加
    assert len(rq.split()) == len(set(rq.split())) or "重复" not in rq
    # 异常/空查询回退原句
    out_empty = await node({"original_query": "", "out_of_scope": False}, {})
    assert out_empty["rewritten_query"] == ""
    # 无公司/年份时不追加空 token
    out_plain = await node({"original_query": "研发投入情况", "out_of_scope": False}, {})
    assert out_plain["rewritten_query"] == "研发投入情况"


async def test_rewrite_query_llm_semantic() -> None:
    """LLM 语义改写胜出：提供 llm 时用其改写结果；LLM 抛错回退确定性（不崩）。"""
    from demomcp.graph.nodes import make_rewrite_query
    from demomcp.interfaces.types import ChatResponse
    from demomcp.providers.llm.mock import MockLLM

    llm = MockLLM([ChatResponse(stop_reason="end_turn", text="比亚迪 2024 年研发投入 研发费用")])
    node = make_rewrite_query(llm)
    out = await node({"original_query": "比亚迪去年研发花多少", "out_of_scope": False}, {})
    assert out["rewritten_query"] == "比亚迪 2024 年研发投入 研发费用"

    class _Boom:
        async def chat(self, **kwargs):
            raise ConnectionError("boom")

    out2 = await make_rewrite_query(_Boom())({"original_query": "比亚迪去年研发花多少", "out_of_scope": False}, {})
    assert out2["rewritten_query"]  # 兜底到确定性改写，仍非空


def test_rewrite_query_domain_expansion() -> None:
    """口语→年报措辞：海外/乘用车/拓展规划 应补出 汽车/整车/出海/全球化布局 等报告术语。"""
    from demomcp.graph.nodes import _deterministic_rewrite

    rq = _deterministic_rewrite("比亚迪2025年海外乘用车业务拓展规划")
    assert "比亚迪" in rq and "2025" in rq
    for term in ("汽车", "新能源汽车", "整车", "海外市场", "全球化布局", "出海"):
        assert term in rq, f"缺 {term}: {rq}"


def test_fin_terms_cover_domain_vocab() -> None:
    """FIN_TERMS 扩展后：海外/乘用车/产销量 进入 concepts（供 BM25 关键词扩展）。"""
    from demomcp.rag.query_build import extract_concepts

    concepts = extract_concepts("海外乘用车出口产销量")
    assert "海外" in concepts and "乘用车" in concepts and "产销量" in concepts


# ---------------------------------------------------------------------------
# 报告 skill（快报）注册表 + router 命中
# ---------------------------------------------------------------------------


def _tracker_system(base: str = "base") -> str:
    from demomcp.graph.skills import get_skill

    skill = get_skill("ai_supply_chain_tracker")
    assert skill is not None
    return skill.system_prompt(base)


def test_skill_registry_tracker_template() -> None:
    """快报 skill 的合成器提示词含六段固定结构 + 「数据未接入/不编造」约束，且可扩展注册表能按 id 命中。"""
    from demomcp.graph.skills import SKILLS, get_skill

    assert get_skill("ai_supply_chain_tracker") is not None
    assert get_skill("not_a_skill") is None
    assert get_skill(None) is None
    assert [s.id for s in SKILLS] == ["ai_supply_chain_tracker"]

    text = _tracker_system()
    for marker in ("板块概览", "标的池行情速览", "关键公告", "业绩预告异动", "产业链催化", "一句话研判", "数据未接入", "不编造"):
        assert marker in text, f"缺 {marker}: {text}"


def test_synth_system_not_polluted_by_tracker() -> None:
    """普通意图（无 skill）的 synth_system 不夹带快报模板字段——skill 只在命中时生效。"""
    from demomcp.graph.prompts import synth_system

    assert "一句话研判" not in synth_system("base", intent="market")
    assert "板块概览" not in synth_system("base", intent="report")


def test_should_rag_strategy_with_skill() -> None:
    """skill 命中时 _should_rag/_strategy 由 skill 覆盖；无 skill 时保持原行为。"""
    from demomcp.graph.nodes import _should_rag, _strategy
    from demomcp.graph.skills import get_skill

    tracker = get_skill("ai_supply_chain_tracker")
    assert tracker is not None
    assert _should_rag("report", skill=tracker) is False  # 快报跳过年报 RAG
    assert _strategy("report", skill=tracker) == "auto"   # 快报用默认检索策略
    # 原行为保持
    assert _should_rag("market", skill=None) is False
    assert _should_rag("report", skill=None) is True
    assert _strategy("report", skill=None) == "factual"
    assert _strategy(None, skill=None) == "auto"


async def test_router_selects_skill(make_settings) -> None:
    """router 识别到快报意图 → on_process("intent", …) 携带 skill 字段；无证据时稳定走 fallback（不崩）。"""
    from demomcp.interfaces.types import ChatResponse

    tools = FakeToolProvider(SPECS, {})
    mock = MockLLM([ChatResponse(stop_reason="end_turn", text='{"intent":"report","skill":"ai_supply_chain_tracker","out_of_scope":false}')])
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=mock, tools=tools, config=make_settings()).run(
        "生成 AI 算力产业链今日跟踪快报", on_process=on_process
    )

    intent_ev = next((d for k, d in events if k == "intent"), None)
    assert intent_ev is not None
    assert intent_ev["skill"] == "ai_supply_chain_tracker"
    assert intent_ev["intent"] == "report"
    # 快报应跳过年报 RAG → 空证据 → fallback（稳定、不崩）
    assert result.stopped_reason == "fallback"


async def test_synthesizer_skips_llm_when_render() -> None:
    """skill 提供 render 时，synthesizer 不走 LLM（no llm.chat），由纯代码把 evidence 渲染成六段，structured 带 skill。"""
    from demomcp.graph.nodes import make_synthesizer
    from demomcp.graph.skills import SKILLS

    mock = MockLLM([ChatResponse(stop_reason="end_turn", text="不应被调用")])
    node = make_synthesizer(mock, max_tokens=128, disclaimer="仅供研究参考", base_system="base", skills=SKILLS)
    state = {
        "messages": [{"role": "user", "content": "生成 AI 算力产业链今日跟踪快报"}],
        "original_query": "生成 AI 算力产业链今日跟踪快报",
        "intent": "report",
        "skill": "ai_supply_chain_tracker",
        "evidence": [{"source_type": "tool", "source": "daily", "content": '[{"name":"新易盛","pct_change":4.5,"close":448.08}]'}],
        "tool_results": [],
        "rag_chunks": [],
        "usage": None,
    }
    out = await node(state, {"configurable": {"on_text": None, "on_thinking": None}})

    assert mock.calls == []  # 渲染路径不调用 LLM
    assert "板块概览" in out["final_answer"] and "标的池行情速览" in out["final_answer"]
    assert "数据未接入" in out["final_answer"]  # 板块段无数据 → 显式标注
    assert "新易盛" in out["final_answer"] and "+4.50%" in out["final_answer"]  # 行情行由代码渲染
    assert "仅供研究参考" in out["final_answer"]  # 免责声明已追加
    assert out["structured"]["skill"] == "ai_supply_chain_tracker"
    assert out["structured"]["strategy"] == "auto"
    assert out["stopped_reason"] == "end_turn"


async def test_tool_rag_reveals_only_curated_subset(make_settings) -> None:
    """tool_rag 只把相关性子集喂给 LLM：meta 恒在、相关工具在、不相关被剪、数量受限。"""
    full = [
        ToolSpec("list_apis"), ToolSpec("get_api_info"), ToolSpec("query"), ToolSpec("stock_basic"),
        ToolSpec("daily"), ToolSpec("adj_factor"), ToolSpec("income"), ToolSpec("fina_indicator"),
        ToolSpec("bond_basic"), ToolSpec("index_daily"), ToolSpec("top_list"),
    ]
    tools = FakeToolProvider(
        full,
        {"income": ToolResult(content='{"ok":true,"data":{"revenue":123},"source":{"params":{"period":"20241231"}}}', is_error=False)},
    )
    mock = MockLLM([
        _router_response('{"intent":"report","out_of_scope":false}'),
        ChatResponse(stop_reason="end_turn", text="贵州茅台 营收 财务"),
        _tool_use_response("c1", "income", {"period": "20241231"}),
        ChatResponse(stop_reason="end_turn", text="营收数据已返回。"),
    ])
    agent = Agent(llm=mock, tools=tools, config=make_settings(tool_max_revealed=4))
    await agent.run("贵州茅台营收")

    # call 1 = tool_rag 选工具轮：收到的是裁剪后的子集
    revealed = mock.calls[2]["tools"]
    rnames = [t.name for t in revealed]
    assert "list_apis" in rnames and "query" in rnames  # meta 恒在
    assert "income" in rnames  # 相关工具保留
    assert "bond_basic" not in rnames  # 不相关被剪
    assert len(rnames) <= 4 + 4  # meta(4) + cap(4)


# ---------------------------------------------------------------------------
# agentic tool loop：多轮取数（tool_rag → tool_rag 条件自环）
# ---------------------------------------------------------------------------


async def test_agentic_loop_two_round_then_synthesize(make_settings) -> None:
    """真实两轮取数：LLM 第 1 轮选 A，看到结果后再选 B，再判定「数据已足够」停止 → 合成。

    验证：证据跨轮累积、RAG 只查一次、loop_turn 事件状态序、两工具都进 structured.sources。
    """
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(
        SPECS,
        {
            "stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False),
            "stock_financials": ToolResult(content='[{"revenue":100}]', is_error=False),
        },
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 区间 财务"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮 → A
            _tool_use_response("c2", "stock_financials", {"stock": "比亚迪"}),   # 第 2 轮 → B
            _stop_response(),  # 第 3 轮：看到 A、B 数据后判定已足够 → 停止
            ChatResponse(stop_reason="end_turn", text="区间与财务如下。"),
        ]
    )
    retriever = _fake_retriever([_rag_chunk()])
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "比亚迪区间与财务"}], "original_query": "比亚迪区间与财务",
             "out_of_scope": False, "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    out = await graph.ainvoke(state, config={"configurable": {"on_process": on_process}})

    assert out["stopped_reason"] == "end_turn"
    assert [name for name, _ in tools.calls] == ["stock_price_range", "stock_financials"]
    assert len(out["tool_results"]) == 2
    assert retriever.calls == 1  # RAG 只查一次（跨两轮不重复检索，防重复证据）
    assert len(mock.calls) == 6  # router / rewrite / 轮1 / 轮2 / 停止判定 / synthesizer
    st = out["structured"]
    assert any(s["type"] == "tool" and s["title"] == "stock_price_range" for s in st["sources"])
    assert any(s["type"] == "tool" and s["title"] == "stock_financials" for s in st["sources"])
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["continue", "continue", "stop"]
    assert [d["round"] for d in loop_turns] == [1, 2, 3]


async def test_agentic_loop_max_iterations_cutoff(make_settings) -> None:
    """max_iterations=2：第 2 轮后即达上限 → 强制收尾生成，不会再多调一轮工具。"""
    cfg = make_settings(max_iterations=2)
    tools = FakeToolProvider(
        SPECS,
        {
            "stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False),
            "stock_financials": ToolResult(content='[{"revenue":100}]', is_error=False),
        },
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 区间 财务"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮 (continue)
            _tool_use_response("c2", "stock_financials", {"stock": "比亚迪"}),   # 第 2 轮 (max-reached)
            ChatResponse(stop_reason="end_turn", text="结果如下。"),  # synthesizer（上限后强制收尾）
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪区间与财务", on_process=on_process)

    assert result.stopped_reason == "end_turn"
    assert [name for name, _ in tools.calls] == ["stock_price_range", "stock_financials"]  # 没有第 3 轮
    assert len(result.tool_results) == 2
    assert len(mock.calls) == 5  # router / rewrite / 轮1 / 轮2 / synthesizer（无停止判定轮）
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["continue", "max-reached"]
    assert [d["round"] for d in loop_turns] == [1, 2]


async def test_agentic_loop_max_zero_evidence_fallback(make_settings) -> None:
    """max_iterations=2 且所有工具都返回空 → 到达上限仍无证据 → 未取到可靠数据兜底（不崩、不无限循环）。"""
    cfg = make_settings(max_iterations=2)
    tools = FakeToolProvider(
        SPECS,
        {
            "stock_price_range": ToolResult(content="[]", is_error=False),  # 空数据
            "stock_financials": ToolResult(content="[]", is_error=False),
        },
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 区间"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮 (continue)
            _tool_use_response("c2", "stock_financials", {"stock": "比亚迪"}),   # 第 2 轮 (max-reached)
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪区间", on_process=on_process)

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert [name for name, _ in tools.calls] == ["stock_price_range", "stock_financials"]
    assert len(result.tool_results) == 2
    assert len(mock.calls) == 4  # router / rewrite / 轮1 / 轮2（不到 synthesizer）
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["no-progress", "max-reached"]


async def test_agentic_loop_events_carry_round(make_settings) -> None:
    """循环可视化：每轮 stage/plan/aggregate/loop_turn 都携带轮次号（round），前端可按轮渲染。"""
    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 价格"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),  # 第 1 轮
            _stop_response(),  # 停止
            ChatResponse(stop_reason="end_turn", text="价格如下。"),  # synthesizer
        ]
    )
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    await Agent(llm=mock, tools=tools, config=cfg).run("比亚迪价格", on_process=on_process)

    kinds = [k for k, _ in events]
    assert "loop_turn" in kinds
    assert [d.get("round") for k, d in events if k == "stage" and d.get("stage") == "tool_rag"] == [1, 2]
    assert [d.get("round") for k, d in events if k == "plan"] == [1]
    assert [d.get("round") for k, d in events if k == "aggregate"] == [1, 2]
    loop_turn = next(d for k, d in events if k == "loop_turn")
    assert loop_turn["round"] == 1
    assert loop_turn["tools"] == ["stock_price_range"]
    assert loop_turn["status"] == "continue"


class _AlwaysWantMoreLLM:
    """每轮选工具都返回 tool_use（模拟「总想要更多、从不停下」的 LLM）——用于测进展守卫。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, temperature=None, on_text=None, on_thinking=None):
        self.calls += 1
        if not tools:  # router（首个）/ rewrite_query
            return ChatResponse(
                stop_reason="end_turn",
                text='{"intent":"market","out_of_scope":false}' if self.calls == 1 else "海光信息 财务状况",
            )
        name = tools[0].name
        cid = f"c{self.calls}"
        return ChatResponse(
            stop_reason="tool_use",
            tool_uses=[ToolUse(id=cid, name=name, input={})],
            raw_content={"content": None, "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}]},
        )

    def assistant_message(self, resp):
        return {"role": "assistant", "content": resp.text or None}

    def tool_results_messages(self, results):
        return [{"role": "tool", "tool_call_id": tu.id, "content": tr.content} for tu, tr in results]


async def test_agentic_loop_stops_on_no_progress(make_settings) -> None:
    """工具反复返回「无权限/空数据」→ 进展守卫：2 轮无进展即止（不烧满 max_iterations，防死循环）。

    复现场景：LLM 每轮仍要工具，但工具恒回 `{code!=0, data:[]}`（无可用数据）。改前会烧满 10 轮（12 次 LLM 调用），
    改后 2 轮即 no_evidence 兜底。回归「修死循环」。
    """
    cfg = make_settings(max_iterations=10)
    tools = FakeToolProvider(
        SPECS,
        {s.name: ToolResult(content='{"code":-9000,"msg":"无权限","row_count":0,"data":[]}', is_error=False) for s in SPECS},
    )
    llm = _AlwaysWantMoreLLM()
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    result = await Agent(llm=llm, tools=tools, config=cfg).run("海光信息最近一年的财务状况", on_process=on_process)

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert llm.calls == 4  # router / rewrite_query / 轮1 / 轮2（不再烧满 10 轮）
    loop_turns = [d for k, d in events if k == "loop_turn"]
    assert [d["status"] for d in loop_turns] == ["no-progress", "no-progress"]
    assert [d["round"] for d in loop_turns] == [1, 2]


async def test_rag_skipped_for_non_corpus_company(make_settings) -> None:
    """问非比亚迪/宁德时代公司（如贵州茅台）→ RAG 直接跳过（不返回别家年报切片），仅工具路作答。"""
    from demomcp.graph.builder import build_research_graph

    cfg = make_settings()
    tools = FakeToolProvider(SPECS, {"stock_price_range": ToolResult(content='[{"close":1604.9}]', is_error=False)})
    mock = MockLLM(
        [
            _router_response('{"intent":"report","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="贵州茅台 研发投入"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"stock": "600519.SH"}),
            _stop_response(),
            ChatResponse(stop_reason="end_turn", text="贵州茅台研发投入相关数据（来自工具）。"),
        ]
    )
    retriever = _fake_retriever(
        [{
            "doc_id": "d1", "doc_title": "比亚迪2024年报", "chunk_index": 0, "text": "研发投入约 X 亿元", "score": 0.9,
            "metadata": {"company": "比亚迪", "year": 2024, "section_path": ["3.2 研发投入"], "page_start": 42,
                         "page_end": 42, "heading": "研发投入", "block_type": "paragraph", "doc_title": "比亚迪2024年报"},
        }]
    )
    graph = build_research_graph(mock, tools, SPECS, max_tokens=cfg.ds_max_tokens, disclaimer=cfg.disclaimer, base_system=cfg.system_prompt, retriever=retriever)
    state = {"messages": [{"role": "user", "content": "贵州茅台研发投入"}], "original_query": "贵州茅台研发投入", "out_of_scope": False,
             "tool_results": [], "evidence": [], "rag_chunks": [], "citations": [], "usage": None}
    out = await graph.ainvoke(state, config={"configurable": {}})

    assert out["stopped_reason"] == "end_turn"
    assert retriever.calls == 0  # RAG 被跳过（语料无贵州茅台）
    structured = out["structured"]
    rag_sources = [s for s in structured["sources"] if s["type"] == "rag"]
    assert rag_sources == []  # 不出现别家年报来源
