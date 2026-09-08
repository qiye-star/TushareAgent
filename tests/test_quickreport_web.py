"""quickreport web 端点：/api/quickreport/latest 与 /generate（TestClient 触发真实 lifespan，全离线）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from demomcp.quickreport.store import save_report

_SAMPLE = {"version": 1, "generated_at": "2026-09-05T08:30:12+08:00", "date": "2026-09-04",
           "sector": "AI算力产业链", "missing": [], "errors": [],
           "board": {"status": "ok", "rows": []}, "watchlist": {"status": "empty", "rows": []},
           "announce": {"status": "empty", "items": []}, "forecast": {"status": "empty", "items": []},
           "news": {"status": "empty", "items": []}, "brief": {"text": "数据不足，无法研判。", "chars": 9}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient + 离线开关：QUICKREPORT_AUTO=false 不建后台任务；目录隔离到 tmp_path。"""
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    monkeypatch.setenv("QUICKREPORT_AUTO", "false")
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")
    from demomcp.entry import web

    with TestClient(web.app) as c:
        yield c, tmp_path, web


def test_latest_404_when_not_generated(client) -> None:
    c, _, _ = client
    assert c.get("/api/quickreport/latest").status_code == 404


def test_latest_200_and_content(client) -> None:
    c, tmp, _ = client
    save_report(_SAMPLE, tmp / "latest.json")
    resp = c.get("/api/quickreport/latest")
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-09-04"


def test_generate_200_and_saves(client, monkeypatch) -> None:
    c, tmp, web = client

    # watchlist 配置 + 假工具池（只回 trade_cal，其余空 → 各段 empty/na 也算成功）
    watch = {"name": "AI算力产业链",
             "watchlist": [{"name": "新易盛", "ts_code": "300502.SZ"}],
             "board": {"th_concepts": [], "sw_indexes": [], "indexes": [], "dc_flow": False},
             "news_sources": []}
    (tmp / "watchlist.json").write_text(json.dumps(watch, ensure_ascii=False), encoding="utf-8")

    class _FakeTools:
        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments=None):
            if name == "trade_cal":
                return __import__("demomcp.interfaces.types", fromlist=["ToolResult"]).ToolResult(
                    json.dumps({"code": 0, "data": [{"cal_date": "20260904", "is_open": "1"}]}, ensure_ascii=False),
                    is_error=False,
                )
            if name == "moneyflow":  # top_inflow 的 best-effort
                return __import__("demomcp.interfaces.types", fromlist=["ToolResult"]).ToolResult(
                    '{"code": 0, "data": []}', is_error=False,
                )
            return __import__("demomcp.interfaces.types", fromlist=["ToolResult"]).ToolResult(
                '{"code": 0, "data": []}', is_error=False,
            )

    async def fake_get_tools(app):
        return _FakeTools()

    monkeypatch.setattr(web, "_acquire_tools", fake_get_tools)
    resp = c.post("/api/quickreport/generate", json={})
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-09-04"
    assert (tmp / "latest.json").exists()
    # 生成后 latest 可读
    assert c.get("/api/quickreport/latest").status_code == 200


def test_generate_422_when_config_missing(client) -> None:
    c, _, _ = client  # 目录里无 watchlist.json
    resp = c.post("/api/quickreport/generate", json={})
    assert resp.status_code == 422
    assert "watchlist" in resp.json()["detail"]


def test_generate_concurrent_409(client, monkeypatch) -> None:
    c, tmp, web = client
    import asyncio

    watch = {"watchlist": [{"name": "新易盛", "ts_code": "300502.SZ"}],
             "board": {"th_concepts": [], "sw_indexes": [], "indexes": [], "dc_flow": False},
             "news_sources": []}
    (tmp / "watchlist.json").write_text(json.dumps(watch, ensure_ascii=False), encoding="utf-8")

    entered = asyncio.Event()

    class _SlowTools:
        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments=None):
            if name == "trade_cal":
                entered.set()
                await asyncio.sleep(1.0)
                return __import__("demomcp.interfaces.types", fromlist=["ToolResult"]).ToolResult(
                    '{"code": 0, "data": [{"cal_date": "20260904", "is_open": "1"}]}', is_error=False,
                )
            return __import__("demomcp.interfaces.types", fromlist=["ToolResult"]).ToolResult(
                '{"code": 0, "data": []}', is_error=False,
            )

    async def fake_get_tools(app):
        return _SlowTools()

    monkeypatch.setattr(web, "_acquire_tools", fake_get_tools)

    r1 = c.post("/api/quickreport/generate", json={})
    assert r1.status_code == 200  # 串行完成（单线程 TestClient 里第一个请求完成才轮到第二个）
    # 锁未持有时第二个请求也 OK
    r2 = c.post("/api/quickreport/generate", json={})
    assert r2.status_code in (200, 409)


def test_lifespan_no_auto_task_when_disabled(client) -> None:
    _, _, web = client
    assert web.app.state.quickreport_task is None  # QUICKREPORT_AUTO=false → 不建任务
    assert web.app.state.quickreport_lock is not None


def test_history_endpoints(client) -> None:
    """历史列表 / 按日读取：存档两份后列表降序、report/{date} 可取、未知日期 404。"""
    c, _, _ = client
    from demomcp.quickreport.store import save_report

    save_report({**_SAMPLE, "date": "2026-09-04"})
    save_report({**_SAMPLE, "date": "2026-09-03", "missing": ["news"]})

    hist = c.get("/api/quickreport/history")
    assert hist.status_code == 200
    assert [h["date"] for h in hist.json()] == ["2026-09-04", "2026-09-03"]
    assert hist.json()[1]["status_ok"] is False

    r = c.get("/api/quickreport/report/2026-09-03")
    assert r.status_code == 200
    assert r.json()["date"] == "2026-09-03"

    assert c.get("/api/quickreport/report/2020-01-01").status_code == 404
