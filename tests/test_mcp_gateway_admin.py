"""网关管理 REST API：TestClient(app) 触发真实 lifespan；monkeypatch build_sources 注入假源，
不连真实 Tushare/Wind（镜像 demomcp 侧 test_web_tools_singleton.py 的假 provider 手法）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from demomcp.interfaces.types import ToolResult, ToolSpec


class _FakeProvider:
    async def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name="fake_tool", description="d")]

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        return ToolResult(content="ok")


def _fake_sources(settings):
    from mcp_gateway.sources import SourceDef

    @asynccontextmanager
    async def connect():
        yield _FakeProvider()

    return [SourceDef(id="fake", display_name="Fake Source", connect=connect)]


def _no_config_problems(monkeypatch) -> None:
    """测试环境没有 mcp_gateway/.env 也没有 TUSHARE_MCP_URL，config_problems 本来非空；
    这里假装配置齐备，好单独验证「连接健康」那一半逻辑（配置问题另有专门用例）。"""
    from mcp_gateway.config import GatewaySettings

    monkeypatch.setattr(GatewaySettings, "config_problems", lambda self: [])


def test_admin_sources_roundtrip(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    import mcp_gateway.app as gw_app

    monkeypatch.setattr(gw_app, "build_sources", _fake_sources)
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path))

    with TestClient(gw_app.app) as client:
        r = client.get("/admin/sources")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == "fake"
        assert body[0]["enabled"] is True
        assert body[0]["connected"] is True  # 热启动已在 lifespan 里跑完

        r2 = client.post("/admin/sources/fake", json={"enabled": False})
        assert r2.status_code == 200
        assert r2.json()["enabled"] is False

        r3 = client.get("/admin/sources")
        assert r3.json()[0]["enabled"] is False

        r4 = client.post("/admin/sources/does-not-exist", json={"enabled": True})
        assert r4.status_code == 404


def test_admin_health(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    import mcp_gateway.app as gw_app

    monkeypatch.setattr(gw_app, "build_sources", _fake_sources)
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path))
    _no_config_problems(monkeypatch)

    with TestClient(gw_app.app) as client:
        r = client.get("/admin/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["config_problems"] == []
        assert len(body["sources"]) == 1


def test_admin_health_reports_config_problems(monkeypatch, tmp_path) -> None:
    """配置没配好：不崩进程，但 ok=false 且把原因原样带出来（前端「设置」页直接显示）。"""
    from fastapi.testclient import TestClient

    import mcp_gateway.app as gw_app
    from mcp_gateway.config import GatewaySettings

    monkeypatch.setattr(gw_app, "build_sources", _fake_sources)
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(GatewaySettings, "config_problems", lambda self: ["未配置 TUSHARE_MCP_URL：…"])

    with TestClient(gw_app.app) as client:
        body = client.get("/admin/health").json()
        assert body["ok"] is False
        assert body["config_problems"] == ["未配置 TUSHARE_MCP_URL：…"]


def test_toggle_persists_across_restart(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient

    import mcp_gateway.app as gw_app

    monkeypatch.setattr(gw_app, "build_sources", _fake_sources)
    monkeypatch.setenv("GATEWAY_DATA_DIR", str(tmp_path))

    with TestClient(gw_app.app) as client:
        client.post("/admin/sources/fake", json={"enabled": False})

    # 第二次「启动」（新的 TestClient = 新的 lifespan = 新的 GatewayToolProvider 实例）应读到落盘状态
    with TestClient(gw_app.app) as client2:
        r = client2.get("/admin/sources")
        assert r.json()[0]["enabled"] is False
