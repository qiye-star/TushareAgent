"""demomcp 侧转发端点：/api/settings/mcp/sources GET/POST 转发到独立部署的 MCP 网关。

关键契约（2026-09-07 事故的直接教训）：网关不可达/没配好时 GET **也返回 200**，把 reachable/error/
config_problems 原样带给前端——前端要把运维态显示出来，而不是把整块「按源开关」隐藏掉。
用假 httpx.AsyncClient 避免真连网关。
"""

from __future__ import annotations

from typing import Any


class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]


class _FakeAsyncClient:
    def __init__(
        self,
        get_response: _FakeResponse | None = None,
        post_response: _FakeResponse | None = None,
        get_exc: Exception | None = None,
    ) -> None:
        self._get_response = get_response
        self._post_response = post_response
        self._get_exc = get_exc
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.get_calls.append(url)
        if self._get_exc is not None:
            raise self._get_exc
        assert self._get_response is not None
        return self._get_response

    async def post(self, url: str, *, json: dict, **kwargs: Any) -> _FakeResponse:
        self.post_calls.append((url, json))
        assert self._post_response is not None
        return self._post_response


def _client(monkeypatch, fake: _FakeAsyncClient, gateway_url: str = "http://gw-host:8766/mcp"):
    from demomcp.entry import web

    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")
    monkeypatch.setenv("MCP_GATEWAY_URL", gateway_url)
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kw: fake)
    return web


def test_get_sources_forwards_to_gateway_health(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake = _FakeAsyncClient(
        get_response=_FakeResponse(
            200,
            {
                "ok": True,
                "config_problems": [],
                "sources": [{"id": "tushare", "display_name": "Tushare 官方 MCP", "enabled": True,
                             "connected": True, "tool_count": 5}],
            },
        )
    )
    web = _client(monkeypatch, fake)

    with TestClient(web.app) as client:
        r = client.get("/api/settings/mcp/sources")
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is True
        assert body["gateway_url"] == "http://gw-host:8766/mcp"
        assert body["error"] is None
        assert body["config_problems"] == []
        assert body["sources"][0]["id"] == "tushare"

    assert fake.get_calls == ["http://gw-host:8766/admin/health"]


def test_get_sources_returns_200_with_diagnostics_when_gateway_down(monkeypatch) -> None:
    """网关没起来：仍 200，reachable=false + error 文案，前端据此渲染「网关不可达」而不是隐藏区块。"""
    import httpx
    from fastapi.testclient import TestClient

    fake = _FakeAsyncClient(get_exc=httpx.ConnectError("connection refused"))
    web = _client(monkeypatch, fake)

    with TestClient(web.app) as client:
        r = client.get("/api/settings/mcp/sources")
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is False
        assert body["sources"] == []
        assert "ConnectError" in body["error"]
        assert body["gateway_url"] == "http://gw-host:8766/mcp"


def test_get_sources_passes_through_config_problems(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake = _FakeAsyncClient(
        get_response=_FakeResponse(
            200, {"ok": False, "config_problems": ["未配置 TUSHARE_MCP_URL：…"], "sources": []}
        )
    )
    web = _client(monkeypatch, fake)

    with TestClient(web.app) as client:
        body = client.get("/api/settings/mcp/sources").json()
        assert body["reachable"] is True
        assert body["config_problems"] == ["未配置 TUSHARE_MCP_URL：…"]


def test_post_source_forwards_to_gateway_admin_api(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake = _FakeAsyncClient(
        post_response=_FakeResponse(200, {"id": "wind", "enabled": False, "connected": False, "tool_count": None})
    )
    web = _client(monkeypatch, fake)

    with TestClient(web.app) as client:
        r = client.post("/api/settings/mcp/sources/wind", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    assert fake.post_calls == [("http://gw-host:8766/admin/sources/wind", {"enabled": False})]


def test_post_source_404_when_gateway_says_unknown(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    fake = _FakeAsyncClient(post_response=_FakeResponse(404, {"detail": "未知数据源"}))
    web = _client(monkeypatch, fake)

    with TestClient(web.app) as client:
        r = client.post("/api/settings/mcp/sources/does-not-exist", json={"enabled": True})
        assert r.status_code == 404


def test_post_source_502_when_gateway_unreachable(monkeypatch) -> None:
    """写操作不做静默降级：网关连不上就是真失败。"""
    import httpx
    from fastapi.testclient import TestClient

    class _BoomClient(_FakeAsyncClient):
        async def post(self, url: str, *, json: dict, **kwargs: Any) -> _FakeResponse:
            raise httpx.ConnectError("connection refused")

    web = _client(monkeypatch, _BoomClient())

    with TestClient(web.app) as client:
        r = client.post("/api/settings/mcp/sources/wind", json={"enabled": False})
        assert r.status_code == 502


def test_effective_admin_url_derives_from_gateway_url(monkeypatch) -> None:
    from demomcp.config.settings import Settings

    monkeypatch.delenv("MCP_GATEWAY_ADMIN_URL", raising=False)
    settings = Settings(_env_file=None, mcp_gateway_url="http://gw-host:8766/mcp")  # type: ignore[call-arg]
    assert settings.effective_mcp_gateway_admin_url == "http://gw-host:8766"


def test_effective_admin_url_explicit_override_wins() -> None:
    from demomcp.config.settings import Settings

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        mcp_gateway_url="http://gw-host:8766/mcp",
        mcp_gateway_admin_url="http://other-host:9999/",
    )
    assert settings.effective_mcp_gateway_admin_url == "http://other-host:9999"


def test_effective_admin_url_from_default_gateway_url() -> None:
    """默认就指向本地网关（不再有「未配置」这种状态）。"""
    from demomcp.config.settings import Settings

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.effective_mcp_gateway_admin_url == "http://127.0.0.1:8766"
