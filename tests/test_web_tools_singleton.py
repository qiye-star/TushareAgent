"""/chat 的工具池必须是进程级单例：验证连续两次请求只建一次到 MCP 网关的连接（不每条消息重连）。

用 TestClient(app) 触发真实 lifespan；monkeypatch `web.mcp_tool_provider`（不联网）与 Agent（不叫真 LLM）。
顺带守住网关化后的关键契约：web 建连时用的**只能**是 `MCP_GATEWAY_URL`——本进程不再直连
Tushare/万得（那是 mcp_gateway/ 那个独立进程的事），所以启动 demomcp 不会带起任何上游 MCP 连接。
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import ClassVar

from demomcp.interfaces.types import AgentResult


class _FakeAgent:
    """哑 Agent：记录构造时收到的 tools 引用，run() 直接返回固定结果，不碰真 LLM。"""

    instances: ClassVar[list[_FakeAgent]] = []

    def __init__(self, *, llm, tools, config, synth_llm=None) -> None:
        self.tools = tools
        _FakeAgent.instances.append(self)

    async def run(self, user_input, *, history=None, mode="agent", on_text=None, on_thinking=None, on_tool=None, on_process=None):
        return AgentResult(final_text="ok", stopped_reason="end_turn", messages=[], tool_results=[], usage=None)


def test_chat_reuses_tool_provider_across_requests(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web

    urls: list[str] = []

    @asynccontextmanager
    async def fake_mcp_tool_provider(url, **kwargs):
        urls.append(url)
        yield object()

    monkeypatch.setenv("MCP_GATEWAY_URL", "http://gw-test:8766/mcp")
    monkeypatch.setattr(web, "mcp_tool_provider", fake_mcp_tool_provider)
    monkeypatch.setattr(web, "Agent", _FakeAgent)
    _FakeAgent.instances.clear()

    with TestClient(web.app) as client:
        r1 = client.post("/chat", json={"message": "第一条"})
        assert r1.status_code == 200
        r2 = client.post("/chat", json={"message": "第二条"})
        assert r2.status_code == 200

    assert urls == ["http://gw-test:8766/mcp"]  # 只建一次连接，且连的就是网关（不是 Tushare/万得）
    assert len(_FakeAgent.instances) == 2  # Agent 仍是每请求新建
    assert _FakeAgent.instances[0].tools is _FakeAgent.instances[1].tools  # 两次拿到同一个 tools 引用


# ---- 工具池热启动（TOOL_POOL_HOT_START=true）：web 加载即建池，不依赖首个 /chat ----


class _FakePool:
    """带 warm_up 的可观测假工具池。"""

    def __init__(self) -> None:
        self.warms = 0

    async def warm_up(self) -> None:
        self.warms += 1


def _counting_provider(enters: list[int], *, fail_entries: int = 0, pool: _FakePool | None = None):
    """计数版 mcp_tool_provider：前 fail_entries 次进入抛错（模拟网关还没起来），其余次返回 pool；
    done 在首个成功进入时置位。"""
    done = threading.Event()

    @asynccontextmanager
    async def fake_mcp_tool_provider(url, **kwargs):
        enters.append(1)
        if len(enters) <= fail_entries:
            raise RuntimeError("boot boom")
        done.set()
        yield pool or _FakePool()

    fake_mcp_tool_provider._done = done  # type: ignore[attr-defined]
    return fake_mcp_tool_provider


def test_hot_start_builds_pool_without_any_chat_request(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web

    enters: list[int] = []
    pool = _FakePool()
    fake = _counting_provider(enters, pool=pool)
    monkeypatch.setattr(web, "mcp_tool_provider", fake)

    with TestClient(web.app):
        # 不发任何 /chat，等热启动任务完成（后台任务经 portal 事件循环跑）
        assert fake._done.wait(timeout=5)  # type: ignore[attr-defined]

    assert enters == [1]  # 无请求也建了池（首个 /chat 不再冷启动）
    assert pool.warms == 1  # warm_up 预热执行


def test_hot_start_failure_retries_until_success(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web

    enters: list[int] = []
    pool = _FakePool()
    fake = _counting_provider(enters, fail_entries=2, pool=pool)
    monkeypatch.setattr(web, "mcp_tool_provider", fake)
    # 退避常数缩到毫秒级，重试循环在测试窗口内跑完
    monkeypatch.setattr(web, "TOOL_HOT_START_BACKOFF_BASE", 0.01)
    monkeypatch.setattr(web, "TOOL_HOT_START_BACKOFF_MAX", 0.01)
    monkeypatch.setattr(web, "Agent", _FakeAgent)
    _FakeAgent.instances.clear()

    with TestClient(web.app) as client:
        assert fake._done.wait(timeout=5)  # type: ignore[attr-defined]
        r1 = client.post("/chat", json={"message": "第一条"})
        r2 = client.post("/chat", json={"message": "第二条"})
        assert r1.status_code == 200
        assert r2.status_code == 200

    assert len(enters) == 3  # 2 次启动失败 + 1 次成功，之后复用
    assert len(_FakeAgent.instances) == 2
    assert _FakeAgent.instances[0].tools is _FakeAgent.instances[1].tools


def test_hot_start_disabled_keeps_lazy_build(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web

    enters: list[int] = []
    pool = _FakePool()
    fake = _counting_provider(enters, pool=pool)
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")
    monkeypatch.setattr(web, "mcp_tool_provider", fake)
    monkeypatch.setattr(web, "Agent", _FakeAgent)
    _FakeAgent.instances.clear()

    with TestClient(web.app) as client:
        assert enters == []  # 无请求 → 不建池（回归冷启动）
        client.post("/chat", json={"message": "第一条"})
        assert enters == [1]  # 首个请求懒建

    assert _FakeAgent.instances[0].tools is pool
