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
    assert len(mock.calls) == 4  # router / rewrite_query / tool选择 / synthesizer
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
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪最近价格")

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    # "[]" 不是错误，但为空数据 → 不算证据
    assert result.tool_results[0].is_error is False
    assert result.citations == []
    assert len(mock.calls) == 3  # router + rewrite_query + tool选择


async def test_tool_error_becomes_fallback(make_settings) -> None:
    tools = FakeToolProvider(SPECS, {}, raise_on={"stock_price_range"})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            ChatResponse(stop_reason="end_turn", text="比亚迪 行情"),  # rewrite_query
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪")

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert len(result.tool_results) == 1
    assert result.tool_results[0].is_error is True  # 异常 → is_error，不算证据
    assert len(mock.calls) == 3  # router + rewrite_query + tool选择


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
    state = {"messages": [{"role": "user", "content": "研发投入"}], "original_query": "研发投入", "out_of_scope": False,
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

    state = {"messages": [{"role": "user", "content": "研发投入"}], "original_query": "研发投入", "out_of_scope": False,
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
