"""quickreport 持久化 + server 薄壳：save/load 往返、损坏容错、generate_from 装配与 ConfigError。"""

from __future__ import annotations

import json

import pytest

from demomcp.interfaces.types import ToolResult
from demomcp.quickreport.config import ConfigError
from demomcp.quickreport.server import generate_from, load_latest, status_from
from demomcp.quickreport.store import (
    list_histories,
    load_report,
    load_report_by_date,
    save_report,
)


class _FakeTools:
    async def list_tools(self):
        return []

    async def call_tool(self, name, arguments=None) -> ToolResult:
        if name == "trade_cal":
            return ToolResult(
                json.dumps({"code": 0, "data": [{"cal_date": "20260904", "is_open": "1"}]}, ensure_ascii=False),
                is_error=False,
            )
        return ToolResult('{"code": 0, "data": []}', is_error=False)


_SAMPLE = {"version": 1, "date": "2026-09-04", "sector": "AI算力产业链", "missing": [], "errors": [],
           "board": {"status": "ok", "rows": []}, "watchlist": {"status": "empty", "rows": []},
           "announce": {"status": "empty", "items": []}, "forecast": {"status": "empty", "items": []},
           "news": {"status": "empty", "items": []}, "brief": {"text": "数据不足，无法研判。", "chars": 9}}


def test_save_load_roundtrip(tmp_path) -> None:
    p = tmp_path / "latest.json"
    save_report(_SAMPLE, p)
    assert load_report(p) == _SAMPLE
    assert load_report(p)["date"] == "2026-09-04"


def test_load_corrupt_returns_none(tmp_path) -> None:
    p = tmp_path / "latest.json"
    p.write_text("{broken", encoding="utf-8")
    assert load_report(p) is None
    p.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert load_report(p) is None  # 非 dict → None


def test_load_missing_returns_none(tmp_path) -> None:
    assert load_report(tmp_path / "nope.json") is None


def test_save_creates_parent_dirs(tmp_path) -> None:
    p = tmp_path / "a" / "b" / "latest.json"
    save_report(_SAMPLE, p)
    assert p.exists()


def test_save_default_archives_history(tmp_path) -> None:
    """默认 save_report 同时写 latest.json 与 history/{date}.json（历史列表/回看）。"""
    import os


    old = os.environ.get("QUICKREPORT_DIR")
    try:
        os.environ["QUICKREPORT_DIR"] = str(tmp_path)
        save_report({**_SAMPLE, "date": "2026-09-04"})
        save_report({**_SAMPLE, "date": "2026-09-03"})
        assert (tmp_path / "latest.json").exists()
        assert (tmp_path / "history" / "2026-09-04.json").exists()
        # 列表按日期降序
        hist = list_histories()
        assert [h["date"] for h in hist] == ["2026-09-04", "2026-09-03"]
        assert hist[0]["status_ok"] is True
        # 按日读取
        assert load_report_by_date("2026-09-03")["date"] == "2026-09-03"
        assert load_report_by_date("2026-01-01") is None
    finally:
        if old is None:
            os.environ.pop("QUICKREPORT_DIR", None)
        else:
            os.environ["QUICKREPORT_DIR"] = old


def test_save_archive_false_skips_history(tmp_path) -> None:
    import os

    old = os.environ.get("QUICKREPORT_DIR")
    try:
        os.environ["QUICKREPORT_DIR"] = str(tmp_path)
        save_report(_SAMPLE, archive=False)
        assert not list_histories()
    finally:
        if old is None:
            os.environ.pop("QUICKREPORT_DIR", None)
        else:
            os.environ["QUICKREPORT_DIR"] = old


def test_history_missing_session_kept_listed(tmp_path) -> None:
    import os

    old = os.environ.get("QUICKREPORT_DIR")
    try:
        os.environ["QUICKREPORT_DIR"] = str(tmp_path)
        save_report({**_SAMPLE, "date": "2026-09-04", "missing": ["news"]})
        hist = list_histories()
        assert hist[0]["date"] == "2026-09-04"
        assert hist[0]["missing"] == ["news"] and hist[0]["status_ok"] is False
    finally:
        if old is None:
            os.environ.pop("QUICKREPORT_DIR", None)
        else:
            os.environ["QUICKREPORT_DIR"] = old


async def test_generate_from_saves_and_returns(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    cfg = json.loads(json.dumps({"watchlist": [{"name": "新易盛", "ts_code": "300502.SZ"}]}))
    from demomcp.quickreport.config import WatchlistConfig

    w = tmp_path / "watchlist.json"
    w.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    parsed = WatchlistConfig.load(w)
    assert parsed is not None
    report = await generate_from(_FakeTools(), parsed, report_date="20260904", stage_timeout=5.0)
    assert report["date"] == "2026-09-04"
    assert (tmp_path / "latest.json").exists()
    assert load_report(tmp_path / "latest.json") == report


async def test_generate_from_when_config_missing_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    with pytest.raises(ConfigError):
        await generate_from(_FakeTools(), None, report_date="20260904")


async def test_generate_from_save_false_does_not_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    from demomcp.quickreport.config import WatchlistConfig

    w = tmp_path / "watchlist.json"
    w.write_text('{"watchlist": [{"name": "x", "ts_code": "000001.SZ"}]}', encoding="utf-8")
    report = await generate_from(_FakeTools(), WatchlistConfig.load(w), report_date="20260904",
                                 stage_timeout=5.0, save=False)
    assert report["version"] == 1
    assert not (tmp_path / "latest.json").exists()


def test_load_latest_and_status(tmp_path, monkeypatch) -> None:
    """原先依赖真实仓库 data/ 目录且断言恒真（读不到也过）——隔离到 tmp 目录并断言实际行为。"""
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    assert load_latest() is None  # 无 latest → None
    save_report(_SAMPLE)
    st = status_from(load_latest())
    assert st is not None and st["date"] == "2026-09-04" and st["missing"] == []
    assert status_from(None) == {"exists": False}


def test_explicit_path_archives_beside_it_not_global_dir(tmp_path, monkeypatch) -> None:
    """2026-09-07 审查回归：显式 path 时归档写到 path 旁的 history/，不再把测试/脚本的产物
    写进全局 QUICKREPORT_DIR 目录（此前测试运行产生过 data/quickreport/history/*.json）。"""
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path / "isolated"))
    p = save_report(_SAMPLE, tmp_path / "nested" / "latest.json")
    assert p.exists()
    assert (tmp_path / "nested" / "history" / "2026-09-04.json").exists()
    # 全局目录不受污染
    assert not (tmp_path / "isolated" / "history" / "2026-09-04.json").exists()
    assert not (tmp_path / "isolated" / "latest.json").exists()


def test_save_report_never_leaves_torn_json(tmp_path, monkeypatch) -> None:
    """原子写（tmp + os.replace）：并发写者下 latest.json 要么旧要么新，不存在半截 JSON。"""
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    import os

    from demomcp.quickreport.config import latest_path

    p = latest_path()
    save_report(_SAMPLE)
    assert p.exists()
    # 目录里没有残留的 tmp 文件（原子写临时文件为 .{name}.{pid}.tmp）
    assert [f.name for f in tmp_path.iterdir() if f.suffix == ".tmp"] == []
    # 覆盖写后仍可完整读回
    save_report({**_SAMPLE, "date": "2026-09-05"})
    assert load_report(p)["date"] == "2026-09-05"
    assert os.path.exists(p)
