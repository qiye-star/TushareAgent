"""MCP 全局运行时开关：GET/POST /api/settings/mcp + /chat 分支（关闭时不触碰工具池，注入 NullToolProvider）。

TestClient(app) 触发真实 lifespan；monkeypatch load_mcp_enabled/save_mcp_enabled 把开关状态重定向到
内存变量，避免读写仓库真实 data/settings/mcp_toggle.json（镜像 test_web_tools_singleton.py 的
monkeypatch mcp_tool_provider 风格，不联网、不叫真 LLM）。
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import ClassVar

from demomcp.interfaces.types import AgentResult
from demomcp.providers.tools.null import NullToolProvider


class _FakeAgent:
    """哑 Agent：记录构造时收到的 tools 引用，run() 直接返回固定结果，不碰真 LLM。"""

    instances: ClassVar[list[_FakeAgent]] = []

    def __init__(self, *, llm, tools, config) -> None:
        self.tools = tools
        _FakeAgent.instances.append(self)

    async def run(self, user_input, *, history=None, mode="agent", on_text=None, on_thinking=None, on_tool=None, on_process=None):
        return AgentResult(final_text="ok", stopped_reason="end_turn", messages=[], tool_results=[], usage=None)


def _isolate_toggle_state(monkeypatch, *, initial: bool):
    """把 load_mcp_enabled/save_mcp_enabled 重定向到内存变量，不落真实 data/settings/mcp_toggle.json。"""
    from demomcp.entry import web

    state = {"enabled": initial}
    monkeypatch.setattr(web, "load_mcp_enabled", lambda: state["enabled"])
    monkeypatch.setattr(web, "save_mcp_enabled", lambda v: state.__setitem__("enabled", v))
    return state


def _fake_provider_factory():
    """计数版 mcp_tool_provider（连网关的那一条）：进入即计数，返回哑 provider（不联网）。"""
    enters = {"count": 0}

    @asynccontextmanager
    async def fake_mcp_tool_provider(url, **kwargs):
        enters["count"] += 1
        yield object()

    return fake_mcp_tool_provider, enters


def test_mcp_status_default_enabled_not_connected(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web

    _isolate_toggle_state(monkeypatch, initial=True)
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")

    with TestClient(web.app) as client:
        r = client.get("/api/settings/mcp")
        assert r.status_code == 200
        assert r.json() == {"enabled": True, "connected": False, "tool_count": None}


def test_toggle_off_then_chat_uses_null_provider_never_touches_pool(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web

    _isolate_toggle_state(monkeypatch, initial=True)
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")
    monkeypatch.setattr(web, "Agent", _FakeAgent)
    _FakeAgent.instances.clear()

    fake_provider, enters = _fake_provider_factory()
    monkeypatch.setattr(web, "mcp_tool_provider", fake_provider)

    with TestClient(web.app) as client:
        r_off = client.post("/api/settings/mcp", json={"enabled": False})
        assert r_off.status_code == 200
        assert r_off.json()["enabled"] is False
        assert r_off.json()["connected"] is False

        r_chat = client.post("/chat", json={"message": "你好"})
        assert r_chat.status_code == 200

    assert enters["count"] == 0  # 全程未建过工具池
    assert len(_FakeAgent.instances) == 1
    assert isinstance(_FakeAgent.instances[0].tools, NullToolProvider)


def test_toggle_on_triggers_one_shot_reconnect(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web

    _isolate_toggle_state(monkeypatch, initial=False)
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")

    fake_provider, enters = _fake_provider_factory()
    monkeypatch.setattr(web, "mcp_tool_provider", fake_provider)

    with TestClient(web.app) as client:
        r_on = client.post("/api/settings/mcp", json={"enabled": True})
        assert r_on.status_code == 200
        assert r_on.json()["enabled"] is True

        for _ in range(50):
            if enters["count"] >= 1:
                break
            time.sleep(0.05)

    assert enters["count"] == 1  # 打开开关触发了一次后台重连尝试


def test_quickreport_generate_returns_503_when_mcp_disabled(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    from demomcp.entry import web
    from demomcp.quickreport.config import WatchlistConfig

    _isolate_toggle_state(monkeypatch, initial=False)
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")
    monkeypatch.setenv("QUICKREPORT_AUTO", "false")

    def _fake_load(cls, path=None):
        from demomcp.quickreport.config import StockCfg

        return WatchlistConfig(watchlist=(StockCfg(name="x", ts_code="000001.SZ"),))

    monkeypatch.setattr(WatchlistConfig, "load", classmethod(_fake_load))

    with TestClient(web.app) as client:
        r = client.post("/api/quickreport/generate", json={"date": None})
        assert r.status_code == 503
