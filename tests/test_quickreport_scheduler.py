"""scheduler 去重守卫 + 时区回归（2026-09-07 审查：`ZoneInfo("UTC")` 在无 tzdata 的 Windows
直接抛 ZoneInfoNotFoundError，调度器首轮循环即死——此处锁定不再使用 tz 数据库直读）。"""

from __future__ import annotations

from datetime import date, datetime

from demomcp.quickreport.config import Schedule, cn_tz
from demomcp.quickreport.scheduler import _is_latest_complete, next_run_dt
from demomcp.quickreport.store import save_report

_SAMPLE = {"version": 1, "date": "2026-09-04", "missing": [], "errors": [],
           "board": {"status": "ok"}, "watchlist": {"status": "ok"}, "announce": {"status": "ok"},
           "forecast": {"status": "ok"}, "news": {"status": "ok"}, "brief": {"text": "x", "chars": 1}}


def test_is_latest_complete(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    assert _is_latest_complete("20260904") is False  # 无 latest
    save_report(_SAMPLE)
    assert _is_latest_complete("20260904") is True
    assert _is_latest_complete("20260903") is False  # 日期不匹配
    save_report({**_SAMPLE, "missing": ["news"]})  # 缺段（降级）→ 不算最新，下次触发重试
    assert _is_latest_complete("20260904") is False


def test_next_run_dt_uses_fixed_utc8_fallback() -> None:
    """Windows 无 tzdata → cn_tz() 回退固定 UTC+8；next_run_dt 不触碰 ZoneInfo("UTC")。"""
    sched = Schedule(hour=8, minute=30)
    before = datetime(2026, 9, 7, 7, 0, tzinfo=cn_tz())
    assert next_run_dt(before, sched).date() == date(2026, 9, 7)
    after = datetime(2026, 9, 7, 9, 0, tzinfo=cn_tz())
    target = next_run_dt(after, sched)
    assert (target.hour, target.minute) == (8, 30)
    assert target.date() == date(2026, 9, 8)
