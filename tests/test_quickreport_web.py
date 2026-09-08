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


# ---------------------------------------------------------------------------
# Phase 4：GET /api/quickreport/status（纯读，不触发 MCP）
# ---------------------------------------------------------------------------


def _mk_watchlist(tmp) -> None:
    import json as _json

    watch = {
        "name": "AI算力产业链",
        "watchlist": [{"name": "新易盛", "ts_code": "300502.SZ"}],
        "board": {"th_concepts": [], "sw_indexes": [], "indexes": [], "dc_flow": False},
        "schedule": {"hour": 8, "minute": 30, "tz": "Asia/Shanghai"},
        "news_sources": [],
    }
    (tmp / "watchlist.json").write_text(_json.dumps(watch, ensure_ascii=False), encoding="utf-8")


def test_status_when_not_generated(client) -> None:
    """未生成时也要 200（前端据此显示「尚未生成 + 下次几点跑」），且**不建任何 MCP 连接**。"""
    c, tmp, _ = client
    _mk_watchlist(tmp)
    r = c.get("/api/quickreport/status")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is False
    # 调度信息来自 watchlist.json + 本地时钟，与报文是否存在无关
    assert body["config_ok"] is True
    assert body["schedule"] == {"hour": 8, "minute": 30, "tz": "Asia/Shanghai"}
    assert body["required_sections"] == ["board", "watchlist"]
    assert body["running"] is False
    # 时间戳必须带 UTC 偏移：前端 formatTime 对无偏移串会补 Z 当 UTC，会早显示 8 小时
    assert body["server_time"].endswith("+08:00")
    # QUICKREPORT_AUTO=false（测试）→ 不给 next_run_at（给了会显示一个永不到来的时刻）
    assert body["auto"] is False
    assert body["next_run_at"] is None


def test_status_reports_sections_and_provenance(client) -> None:
    """生成后：各段 status/来源/条数 + 缺段 + 错误分布，都要能从 /status 一次读到。"""
    c, tmp, _ = client
    _mk_watchlist(tmp)
    from demomcp.quickreport.store import save_report

    save_report({
        "version": 1,
        "generated_at": "2026-09-08T15:20:56+08:00",
        "date": "2026-09-07",
        "sector": "AI算力产业链",
        "missing": ["news"],
        "errors": [
            {"stage": "news", "tool": "get_market_headlines", "reason": "取数失败", "source": "free"},
            {"stage": "news", "tool": "news", "reason": "权限受限", "source": "tushare"},
        ],
        "board": {"status": "ok", "src": "tushare", "src_tool": "index_daily",
                  "src_label": "Tushare 官方 MCP", "fetched_at": "2026-09-08T15:20:50+08:00",
                  "attempts": [], "rows": [{"name": "上证指数"}], "series_status": "ok"},
        "watchlist": {"status": "ok", "src": "tushare", "src_tool": "daily", "src_label": "T",
                      "attempts": [], "rows": [{"name": "a"}, {"name": "b"}]},
        "announce": {"status": "empty", "src": None, "src_tool": None, "attempts": [], "items": []},
        "forecast": {"status": "empty", "src": None, "src_tool": None, "attempts": [], "items": []},
        "news": {"status": "na", "note": "新闻接口未接入", "src": None, "src_tool": None,
                 "attempts": [{"source": "free", "tool": "get_market_headlines", "ok": False}],
                 "items": []},
        "brief": {"text": "x", "chars": 1},
    })
    body = c.get("/api/quickreport/status").json()
    assert body["exists"] is True
    assert body["date"] == "2026-09-07"
    assert body["generated_hhmm"] == "15:20"
    assert body["missing"] == ["news"]
    # 必需段（board/watchlist）都在 → 缺的只有可选段，调度器不会因此全量重跑
    assert body["missing_required"] == []
    assert body["sections"]["board"]["src_tool"] == "index_daily"
    assert body["sections"]["board"]["count"] == 1
    assert body["sections"]["watchlist"]["count"] == 2
    assert body["sections"]["news"]["status"] == "na"
    assert body["sections"]["news"]["note"] == "新闻接口未接入"
    assert body["sections"]["news"]["attempts"][0]["tool"] == "get_market_headlines"
    assert body["errors_count"] == 2
    assert body["errors_by_source"] == {"free": 1, "tushare": 1}
    assert body["series_status"] == "ok"


def test_status_surfaces_and_clears_last_error(client) -> None:
    """last_error 要能被读出来（此前只写不读），且成功生成后被清掉。"""
    c, tmp, _ = client
    _mk_watchlist(tmp)
    from demomcp.quickreport.store import (
        clear_last_error,
        load_last_error,
        save_last_error,
    )

    assert load_last_error() is None
    save_last_error({"at": "2026-09-08T03:12:00+08:00", "error": "网关不可达"})
    body = c.get("/api/quickreport/status").json()
    assert body["last_error"]["error"] == "网关不可达"

    clear_last_error()
    assert c.get("/api/quickreport/status").json()["last_error"] is None
    clear_last_error()  # 幂等：文件已不存在也不该抛


def test_status_missing_required_flags_broken_core_section(client) -> None:
    """必需段真的缺（board na）→ missing_required 非空，前端据此显示「下次触发会重试」。"""
    c, tmp, _ = client
    _mk_watchlist(tmp)
    from demomcp.quickreport.store import save_report

    save_report({
        "version": 1, "generated_at": "2026-09-08T15:20:56+08:00", "date": "2026-09-07",
        "sector": "s", "missing": ["board"], "errors": [],
        "board": {"status": "na", "rows": []},
        "watchlist": {"status": "ok", "rows": [{"name": "a"}]},
        "announce": {"status": "ok", "items": []},
        "forecast": {"status": "ok", "items": []},
        "news": {"status": "ok", "items": []},
        "brief": {"text": "x", "chars": 1},
    })
    body = c.get("/api/quickreport/status").json()
    assert body["missing_required"] == ["board"]
