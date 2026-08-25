"""核心层离线 gate：用 FakeToolProvider + MockLLM 驱动完整 agent 循环（无网络、无代理、无真 LLM）。"""

from __future__ import annotations

from demomcp.agents.agent import Agent
from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse
from demomcp.providers.llm.mock import MockLLM
from demomcp.providers.tools.fake import FakeToolProvider

SPECS = [
    ToolSpec(name="list_apis", description="search", input_schema={"type": "object"}),
    ToolSpec(name="get_api_info", description="info", input_schema={"type": "object"}),
    ToolSpec(name="query", description="query", input_schema={"type": "object"}),
]


def _tool_use_response(call_id: str, name: str, args: dict) -> ChatResponse:
    return ChatResponse(
        stop_reason="tool_use",
        tool_uses=[ToolUse(id=call_id, name=name, input=args)],
        raw_content={
            "content": None,
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": str(args).replace("'", '"')}}
            ],
        },
    )


async def test_full_loop_discover_then_query(make_settings) -> None:
    fake = FakeToolProvider(
        SPECS,
        {
            "list_apis": ToolResult(content="[adj_factor, daily]", is_error=False),
            "get_api_info": ToolResult(content="params: ts_code required", is_error=False),
        },
    )
    mock = MockLLM(
        [
            _tool_use_response("c1", "list_apis", {"q": "复权"}),
            _tool_use_response("c2", "get_api_info", {"api_name": "adj_factor"}),
            ChatResponse(stop_reason="end_turn", text="接口 adj_factor 需必填 ts_code。"),
        ]
    )

    agent = Agent(llm=mock, tools=fake, config=make_settings())
    result = await agent.run("列出复权接口")

    assert result.stopped_reason == "end_turn"
    assert result.final_text == "接口 adj_factor 需必填 ts_code。"
    assert [name for name, _ in fake.calls] == ["list_apis", "get_api_info"]
    assert len(result.tool_results) == 2
    assert all(tr.is_error is False for tr in result.tool_results)
    # 消息帧由 MockLLM 渲染成 OpenAI 风格；修 A 后 end_turn 收尾时最终答复也进 messages
    tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2
    assert result.messages[-1] == {"role": "assistant", "content": "接口 adj_factor 需必填 ts_code。"}


async def test_error_surfaces_as_iserror(make_settings) -> None:
    fake = FakeToolProvider(
        SPECS,
        {"list_apis": ToolResult(content="Error: proxy unreachable", is_error=True)},
    )
    mock = MockLLM(
        [
            _tool_use_response("c1", "list_apis", {}),
            ChatResponse(stop_reason="end_turn", text="已尽力，代理不可达。"),
        ]
    )
    agent = Agent(llm=mock, tools=fake, config=make_settings())
    result = await agent.run("查一下")
    assert result.tool_results[0].is_error is True
    assert result.final_text == "已尽力，代理不可达。"
    # 循环未崩，且继续走了下一次模型轮
    assert len(mock.calls) == 2


async def test_call_tool_raise_becomes_iserror(make_settings) -> None:
    fake = FakeToolProvider(SPECS, {}, raise_on={"query"})
    mock = MockLLM(
        [
            _tool_use_response("c1", "query", {}),
            ChatResponse(stop_reason="end_turn", text="done"),
        ]
    )
    agent = Agent(llm=mock, tools=fake, config=make_settings())
    result = await agent.run("x")
    assert result.tool_results[0].is_error is True
    assert "Error calling query" in result.tool_results[0].content


async def test_max_iterations_cap(make_settings) -> None:
    fake = FakeToolProvider(SPECS, {"query": ToolResult(content="ok")})
    # 永不 end_turn，只循环 max_iterations 次
    mock = MockLLM([_tool_use_response("c1", "query", {})] * 3)
    agent = Agent(llm=mock, tools=fake, config=make_settings(max_iterations=3))
    result = await agent.run("x")
    assert result.stopped_reason == "max_iterations"
    assert len(fake.calls) == 3
