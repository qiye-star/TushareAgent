"""agent.run 对 ExceptionGroup 的处理：取消（CancelledError）透传不误报，真失败返回友好信息并暴露真实原因。"""

import asyncio

import pytest

from demomcp.agents.agent import Agent, _flatten_exceptions
from demomcp.config.settings import Settings


class _LLM:
    def __init__(self) -> None:
        ...

    async def chat(self, *a, **k):
        raise AssertionError("不应调用 llm.chat")

    async def tool_results_messages(self, *a):
        return []


class _Tools:
    async def list_tools(self):
        return []

    async def call_tool(self, *a):
        raise AssertionError("不应调用 call_tool")


class _FakeGraph:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def ainvoke(self, state, config=None):
        raise self._exc


def _agent(exc: BaseException) -> Agent:
    agent = Agent(llm=_LLM(), tools=_Tools(), config=Settings(ds_api_key="x"))
    agent._graphs = {"agent": _FakeGraph(exc)}  # 按 mode 分片缓存；这里只灌 agent 模式那一份
    return agent


def test_flatten_exceptions_unwraps_groups() -> None:
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [ValueError("boom"), KeyError("k")])])
    leaves = _flatten_exceptions(nested)
    assert len(leaves) == 2
    assert isinstance(leaves[0], ValueError)
    assert isinstance(leaves[1], KeyError)


async def test_run_reraises_cancellation_group() -> None:
    agent = _agent(BaseExceptionGroup("g", [asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        await agent.run("比亚迪")


async def test_run_returns_friendly_message_on_group_error() -> None:
    agent = _agent(ExceptionGroup("g", [ConnectionError("Name or service not known")]))
    res = await agent.run("比亚迪")
    assert res.stopped_reason == "error"
    assert "Name or service not known" in res.final_text


async def test_run_returns_friendly_message_on_plain_error() -> None:
    agent = _agent(ConnectionError("upstream timed out"))
    res = await agent.run("比亚迪")
    assert res.stopped_reason == "error"
    assert "upstream timed out" in res.final_text


class _BoomTools:
    """list_tools 抛异常（如 MCP 断线竞态）；call_tool 不应被调用（还没走到那一步）。"""

    async def list_tools(self):
        raise RuntimeError("list_tools transport race")

    async def call_tool(self, *a):
        raise AssertionError("不应调用 call_tool")


async def test_run_returns_friendly_message_when_list_tools_raises() -> None:
    """_get_graph()（经 _get_tool_defs → tools.list_tools()）抛异常也要走 _error_result，
    而不是逃出 run() 之外——2026-09-07 事故：该调用曾在 try 块外，绕过了这层保护。"""
    agent = Agent(llm=_LLM(), tools=_BoomTools(), config=Settings(ds_api_key="x"))
    res = await agent.run("比亚迪")
    assert res.stopped_reason == "error"
    assert "处理失败" in res.final_text
