"""MCP 连接生命周期测试：断线重建（transport 异常→新会话）、保活 ping（防断联）、warm_up/close。

经 `session_factory` 注入假会话 CM，绝不碰真 transport。legacy 注入 session 的路径由
tests/test_tool_provider.py 维持（这里只验证「所有权模式与 legacy 不串」）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from demomcp.providers.tools.mcp import MCPToolProvider, _is_transport_failure


class _FakeSession:
    """假 MCP 会话：call_tool 行为按脚本走（成功 / 业务错 / 抛异常），send_ping 可配置失败。"""

    def __init__(
        self,
        *,
        call_result: str | None = None,
        call_error: Exception | None = None,
        ping_error: Exception | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.call_result = call_result
        self.call_error = call_error
        self.ping_error = ping_error
        self.list_error = list_error
        self.calls = 0
        self.list_calls = 0
        self.pings = 0

    async def call_tool(self, name, arguments=None, read_timeout_seconds=None) -> Any:
        self.calls += 1
        if self.call_error is not None:
            raise self.call_error
        content = type("PlainTextContent", (), {"type": "text", "text": self.call_result})()
        return type("CallToolResult", (), {"content": [content], "isError": False})()

    async def list_tools(self) -> Any:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        tool = type("Tool", (), {"name": "query", "description": "x", "inputSchema": {}})()
        return type("ListToolsResult", (), {"tools": [tool]})()

    async def send_ping(self) -> Any:
        self.pings += 1
        if self.ping_error is not None:
            raise self.ping_error
        return type("EmptyResult", (), {})()


class _Factory:
    """计数会话工厂：产出 _FakeSession 序列，记录 aenter/aexit，支持第 N 次连接失败。"""

    def __init__(self, sessions: list[_FakeSession], *, fail_connects: set[int] | None = None) -> None:
        self.sessions = sessions
        self.fail_connects = fail_connects or set()
        self.aenter = 0
        self.aexit = 0
        self.connect_task: asyncio.Task[Any] | None = None
        self._threads: list[_FakeSession] = []

    @asynccontextmanager
    async def __call__(self):
        self.aenter += 1
        i = self.aenter - 1
        if i in self.fail_connects:
            # 模拟跑到一半失败：嵌套资源已进入 → 需被 stack 清理
            @asynccontextmanager
            async def _inner():
                yield "inner"
            async with _inner():
                raise httpx.ConnectError("connect refused")
        if i < len(self.sessions):
            session = self.sessions[i]
        else:  # 超出脚本 → 最后一个会话复用（保活重连用）
            session = self.sessions[-1]
        self._threads.append(session)
        try:
            yield session
        finally:
            self.aexit += 1


def _ok_session(text: str = "[{\"ts\": 1}]") -> _FakeSession:
    return _FakeSession(call_result=text)


# ---- _is_transport_failure ----

def test_is_transport_failure_classification() -> None:
    assert _is_transport_failure(httpx.ConnectError("boom"))
    assert _is_transport_failure(httpx.ReadTimeout("boom"))
    assert _is_transport_failure(httpx.HTTPStatusError("boom", request=None, response=None))
    assert _is_transport_failure(ConnectionError("boom"))
    assert _is_transport_failure(TimeoutError("boom"))
    assert _is_transport_failure(McpError(ErrorData(code=-32000, message="Connection closed")))
    assert _is_transport_failure(McpError(ErrorData(code=408, message="Timed out while waiting for response")))
    assert not _is_transport_failure(McpError(ErrorData(code=-32601, message="Method not found")))
    assert not _is_transport_failure(RuntimeError("has an output schema but did not return"))
    # 2026-09-07 事故：_keepalive_loop 的 _invalidate() 与并发 list_tools()/call_tool() 竞态时，
    # 会在已关闭的 anyio 流上读出这两种异常——本质是传输层失败，必须能被识别才会触发重建。
    assert _is_transport_failure(anyio.ClosedResourceError())
    assert _is_transport_failure(anyio.BrokenResourceError())


# ---- call_tool 断线重建 ----

async def test_transport_exception_rebuilds_session_once() -> None:
    factory = _Factory([_FakeSession(call_error=httpx.ConnectError("dead")), _ok_session()])
    provider = MCPToolProvider(session_factory=factory, retries=3)
    result = await provider.call_tool("query", {"ts_code": "600000.SH"})

    assert result.is_error is False
    assert factory.aenter == 2  # 第一次失败 → 重建一次
    assert factory.aexit == 1  # 被作废的旧会话已关闭
    assert provider._session is factory.sessions[1]
    await provider.close()
    assert factory.aexit == 2


async def test_business_error_never_rebuilds() -> None:
    session = _FakeSession(call_result='{"code": 1, "msg": "no permission"}')
    factory = _Factory([session])
    provider = MCPToolProvider(session_factory=factory, retries=2)
    result = await provider.call_tool("query", {})

    assert result.is_error is False  # 业务失败语义由 retries 之后映射，本用例断言“无重建、同一会话”
    assert factory.aenter == 1
    assert session.calls == 2  # 重试在同一个会话上进行
    await provider.close()


async def test_reconnect_disabled_uses_single_session() -> None:
    factory = _Factory([_FakeSession(call_error=httpx.ConnectError("dead"))])
    provider = MCPToolProvider(session_factory=factory, retries=2, reconnect=False)
    result = await provider.call_tool("query", {})

    assert result.is_error is True
    assert "after 2 tries" in result.content
    assert factory.aenter == 1  # 不重建
    await provider.close()


async def test_nontransport_exception_does_not_rebuild() -> None:
    session = _FakeSession(call_error=McpError(ErrorData(code=-32601, message="Method not found")))
    factory = _Factory([session])
    provider = MCPToolProvider(session_factory=factory, retries=2)
    result = await provider.call_tool("query", {})

    assert result.is_error is True
    assert factory.aenter == 1
    assert session.calls == 2  # 同一会话重试（协议级错误不重建）
    await provider.close()


async def test_list_tools_transport_exception_rebuilds_session_once() -> None:
    """list_tools 撞上传输层异常（如保活竞态下的 ClosedResourceError）→ 作废+重建一次，重试成功。"""
    factory = _Factory([_FakeSession(list_error=anyio.ClosedResourceError()), _ok_session()])
    provider = MCPToolProvider(session_factory=factory, retries=3)
    specs = await provider.list_tools()

    assert [s.name for s in specs] == ["query"]
    assert factory.aenter == 2  # 第一次失败 → 重建一次
    assert factory.aexit == 1  # 被作废的旧会话已关闭
    assert provider._session is factory.sessions[1]
    await provider.close()


async def test_list_tools_nontransport_exception_propagates() -> None:
    """非传输类异常：不重建，重试耗尽后原样 raise（list_tools 没有 ToolResult 可折叠）。"""
    session = _FakeSession(list_error=McpError(ErrorData(code=-32601, message="Method not found")))
    factory = _Factory([session])
    provider = MCPToolProvider(session_factory=factory, retries=2)

    with pytest.raises(McpError):
        await provider.list_tools()

    assert factory.aenter == 1  # 协议级错误不重建
    assert session.list_calls == 2  # 同一会话重试
    await provider.close()


async def test_legacy_injected_session_never_uses_factory() -> None:
    owning_session = _FakeSession(call_error=httpx.ConnectError("dead"))

    async def sentinel_factory():  # 用了就炸
        raise AssertionError("legacy 模式下不应调用 session_factory")

    provider = MCPToolProvider(owning_session, retries=2, session_factory=sentinel_factory)
    result = await provider.call_tool("query", {})

    assert result.is_error is True
    assert "after 2 tries" in result.content
    assert owning_session.calls == 2
    await provider.close()  # legacy close 是 no-op，不会关掉注入的会话


# ---- 保活（防断联） ----

async def test_keepalive_ping_failure_triggers_background_restart() -> None:
    first = _FakeSession(ping_error=httpx.ConnectError("nat 掐线"))
    second = _FakeSession(call_result="[]")
    factory = _Factory([first, second])
    provider = MCPToolProvider(session_factory=factory, keepalive_interval=0.01, retries=1)

    # 首次连接（warm_up 也顺带验证）
    await provider.warm_up()
    assert factory.aenter == 1
    # 保活是后台任务：等它完成第一次 ping（该 ping 注定失败 → 触发重建）
    for _ in range(400):
        if first.pings >= 1:
            break
        await asyncio.sleep(0.01)
    assert first.pings >= 1

    # 等第二条会话被保活重建
    for _ in range(200):
        if factory.aenter >= 2:
            break
        await asyncio.sleep(0.01)
    assert factory.aenter == 2, "保活检测到断线应重建会话"
    assert provider._session is second

    await provider.close()
    assert factory.aexit == 2  # 关停时新旧会话都关闭


async def test_keepalive_ping_method_not_found_does_not_restart() -> None:
    session = _FakeSession(ping_error=McpError(ErrorData(code=-32601, message="Method not found")))
    factory = _Factory([session])
    provider = MCPToolProvider(session_factory=factory, keepalive_interval=0.01, retries=1)
    await provider.warm_up()
    assert factory.aenter == 1

    await asyncio.sleep(0.05)  # 约 5 个周期
    assert factory.aenter == 1, "协议级拒绝（服务端活着）不应触发重建"
    await provider.close()


def test_factory_returns_callable_not_cm() -> None:
    """_factory 必须返回零参工厂（调用一次产出 CM），而不是已调用完的 CM 对象。

    回归：曾直接返回 `_streamable_session_factory(url, ...)` 的调用结果（本身就是
    asynccontextmanager 的 CM），调用方再 `factory()()` 会触发
    `AsyncContextDecorator.__call__() missing 1 required positional argument: 'func'`。
    """
    provider = MCPToolProvider(url="https://example.invalid/mcp")
    factory = provider._factory()
    cm = factory()
    assert hasattr(cm, "__aenter__")
    assert hasattr(cm, "__aexit__")


async def test_real_factory_path_via_url(monkeypatch) -> None:
    """无 session_factory、只有 url 的 owned 模式走真实 _factory 分支（monkeypatch 底层连接）。"""
    entered = 0
    session = _FakeSession(call_result="[]")

    @asynccontextmanager
    async def fake_streamable(url, *, headers=None, timeout=30.0):
        nonlocal entered
        entered += 1
        assert url == "https://example.invalid/mcp"
        yield session

    monkeypatch.setattr("demomcp.providers.tools.mcp._streamable_session_factory", fake_streamable)

    provider = MCPToolProvider(url="https://example.invalid/mcp", retries=1)
    await provider._ensure_connected()
    assert entered == 1
    assert provider._session is session

    result = await provider.call_tool("query", {})
    assert result.is_error is False
    await provider.close()
    assert provider._closed is True


# ---- warm_up / close ----

async def test_warm_up_connects_and_lists() -> None:
    session = _FakeSession(call_result="[]")
    factory = _Factory([session])
    provider = MCPToolProvider(session_factory=factory, retries=1)
    await provider.warm_up()

    assert factory.aenter == 1
    assert session.list_calls == 1
    assert isinstance(provider._session, _FakeSession)
    await provider.close()


async def test_close_is_idempotent_and_stops_keepalive() -> None:
    session = _FakeSession()
    factory = _Factory([session])
    provider = MCPToolProvider(session_factory=factory, keepalive_interval=0.01, retries=1)
    await provider.warm_up()

    await provider.close()
    await provider.close()  # 幂等
    assert factory.aexit == 1
    # close 后使用被拒（_ensure_connected 抛 RuntimeError；call_tool 会把任何异常吞成 is_error 结果）
    with pytest.raises(RuntimeError):
        await provider._ensure_connected()
    result = await provider.call_tool("query", {})
    assert result.is_error is True

    # close 后保活任务已停：不会再连接（aenter 不增长）
    await asyncio.sleep(0.03)
    assert factory.aenter == 1


async def test_cancel_during_connect_closes_partial_stack() -> None:
    """连接跑到一半被取消：已进入的半截资源必须被关闭，不泄漏栈。"""
    first = _FakeSession(call_result="[]")
    factory = _Factory([first])

    def slow_factory():  # aenter 里挂起 → 任务取消时 enter_async_context 抛 CancelledError
        @asynccontextmanager
        async def _cm():
            factory.aenter += 1
            await asyncio.sleep(60)
            yield first
        return _cm()

    provider = MCPToolProvider(session_factory=slow_factory, retries=1)

    task = asyncio.create_task(provider._ensure_connected())
    await asyncio.sleep(0.01)  # 让 enter_async_context 进入 slow_factory 内部
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 关键断言：_restart 的 BaseException 分支做了 aclose，provider 不持有半截栈
    assert provider._stack is None
    assert provider._session is None
    await provider.close()


async def test_list_tools_zero_retries_still_attempts_once() -> None:
    """2026-09-07 审查回归：DEMO_MCP_RETRIES=0 时 list_tools 原来的 range(0) 落到 `raise None`
    （TypeError）。retries=0 语义应为「不重试但仍尝试一次」（与 call_tool 一致）。"""
    session = _FakeSession(call_result="ok")
    provider = MCPToolProvider(session=session, retries=0)
    specs = await provider.list_tools()
    assert len(specs) == 1 and specs[0].name == "query"
    assert session.list_calls == 1

    result = await provider.call_tool("query", {})
    assert result.is_error is False
    assert session.calls == 1


async def test_keepalive_late_failure_does_not_kill_fresh_session() -> None:
    """2026-09-07 审查回归：保活 ping 的失败只应作废「它 ping 的那个会话」——
    call_tool 已重建出新会话时，保活迟到失败不得拆掉新会话（连锁重建 + 重试预算耗尽）。"""
    session1 = _FakeSession(call_error=anyio.BrokenResourceError("stream closed"))
    session2 = _FakeSession()
    factory = _Factory([session1, session2])
    provider = MCPToolProvider(session_factory=factory, retries=1, reconnect=True)

    s1 = await provider._ensure_connected()
    assert s1 is session1
    # call_tool 传输失败 → 吞成 is_error 并作废 s1（此时重建尚未发生）
    result = await provider.call_tool("query", {})
    assert result.is_error is True and provider._session is None
    # 下次取连接时经 _restart 拿到 session2
    s2 = await provider._ensure_connected()
    assert s2 is session2
    # 保活对旧 session（s1）的迟到失败：_invalidate(s1) 身份检查 → 不拆 session2
    await provider._invalidate(session1)
    assert provider._session is session2
    await provider.close()
