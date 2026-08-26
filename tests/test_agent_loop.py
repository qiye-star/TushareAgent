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
    assert len(mock.calls) == 3  # router / tool选择 / synthesizer


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
            ChatResponse(stop_reason="end_turn", text="我不知道要用什么工具。"),
        ]
    )
    agent = Agent(llm=mock, tools=FakeToolProvider(SPECS, {}), config=make_settings())
    result = await agent.run("比亚迪")

    assert result.stopped_reason == "fallback"
    assert "未能识别出可用的取数工具" in result.final_text
    assert result.tool_results == []
    assert len(mock.calls) == 2  # router + tool选择


async def test_empty_evidence_fallback(make_settings) -> None:
    tools = FakeToolProvider(
        SPECS, {"stock_price_range": ToolResult(content="[]", is_error=False)}
    )
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
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
    assert len(mock.calls) == 2


async def test_tool_error_becomes_fallback(make_settings) -> None:
    tools = FakeToolProvider(SPECS, {}, raise_on={"stock_price_range"})
    mock = MockLLM(
        [
            _router_response('{"intent":"market","out_of_scope":false}'),
            _tool_use_response("c1", "stock_price_range", {"name": "比亚迪"}),
        ]
    )
    agent = Agent(llm=mock, tools=tools, config=make_settings())
    result = await agent.run("比亚迪")

    assert result.stopped_reason == "fallback"
    assert "未能取到可靠数据" in result.final_text
    assert len(result.tool_results) == 1
    assert result.tool_results[0].is_error is True  # 异常 → is_error，不算证据
    assert len(mock.calls) == 2


class _RaisingLLM:
    """LLM 逐次调用都抛异常（模拟瞬时网络/流失败）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, on_text=None, on_thinking=None):
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

    async def chat(self, *, messages, tools, system=None, max_tokens=8192, stream=True, on_text=None, on_thinking=None):
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
