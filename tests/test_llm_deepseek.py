"""LLM 后端离线测试：OpenAI 工具映射 + finish_reason 归一化 + 消息帧（纯逻辑，不联网）。"""

from __future__ import annotations

from demomcp.interfaces.types import ChatResponse, ToolResult, ToolSpec, ToolUse
from demomcp.providers.llm.deepseek import (
    DeepSeekLLMClient,
    map_finish,
    to_openai_tool,
)


def test_to_openai_tool_shape() -> None:
    spec = ToolSpec(name="query", description="desc", input_schema={"type": "object", "properties": {}})
    tool = to_openai_tool(spec)
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "query"
    assert tool["function"]["parameters"] == spec.input_schema


def test_to_openai_tool_empty_schema_default() -> None:
    spec = ToolSpec(name="query", input_schema={})
    tool = to_openai_tool(spec)
    assert tool["function"]["parameters"]["type"] == "object"


def test_map_finish_normalization() -> None:
    assert map_finish("tool_calls", True) == "tool_use"
    assert map_finish("stop", True) == "tool_use"
    assert map_finish("stop", False) == "end_turn"
    assert map_finish("length", False) == "max_tokens"
    assert map_finish(None, False) == "end_turn"
    assert map_finish(None, True) == "tool_use"


def test_deepseek_assistant_message_and_tool_results() -> None:
    client = DeepSeekLLMClient(api_key="x", base_url="http://gw", model="m")
    tu = ToolUse(id="c1", name="query", input={"x": 1})
    resp = ChatResponse(
        stop_reason="tool_use",
        tool_uses=[tu],
        raw_content={
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "query", "arguments": "{}"}}
            ],
        },
    )
    msg = client.assistant_message(resp)
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["id"] == "c1"
    results = client.tool_results_messages([(tu, ToolResult(content="ok"))])
    assert results == [{"role": "tool", "tool_call_id": "c1", "content": "ok"}]


async def test_mock_llm_pops_and_records() -> None:
    from demomcp.providers.llm.mock import MockLLM

    resp = ChatResponse(stop_reason="end_turn", text="hi")
    mock = MockLLM([resp])
    out = await mock.chat(messages=[{"role": "user", "content": "q"}], tools=[])
    assert out.text == "hi"
    assert len(mock.calls) == 1


class _FakeMessage:
    def __init__(self, content: str | None = "ok") -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self) -> None:
        self.message = _FakeMessage()
        self.finish_reason = "stop"


class _FakeCompletion:
    def __init__(self) -> None:
        self.choices = [_FakeChoice()]
        self.usage = None


async def test_chat_omits_tool_choice_when_tools_empty(monkeypatch) -> None:
    """tools=[] 时不应发 tool_choice——OpenAI 兼容 API 在没有 tools 却收到 tool_choice 时会报错

    （2026-09-07 事故：MCP 全局开关关闭后 NullToolProvider 让 tools=[] 首次可达，之前从未触发过）。
    """
    client = DeepSeekLLMClient(api_key="x", base_url="http://gw", model="m")
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeCompletion()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    await client.chat(messages=[{"role": "user", "content": "hi"}], tools=[], stream=False)

    assert "tools" not in captured
    assert "tool_choice" not in captured


async def test_chat_includes_tool_choice_when_tools_present(monkeypatch) -> None:
    client = DeepSeekLLMClient(api_key="x", base_url="http://gw", model="m")
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _FakeCompletion()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    spec = ToolSpec(name="query", description="d", input_schema={"type": "object", "properties": {}})
    await client.chat(messages=[{"role": "user", "content": "hi"}], tools=[spec], stream=False)

    assert captured["tool_choice"] == "auto"
    assert captured["tools"][0]["function"]["name"] == "query"
