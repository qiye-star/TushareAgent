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


def test_is_latest_complete_ignores_optional_na(tmp_path, monkeypatch) -> None:
    """**可选段**缺失（news na）不再让每次启动都全量重跑。

    这是刻意的行为变化：改造前只要 missing 非空就判「不是最新」，而 news 长期 40203 无权限
    → 每次进程启动都把整套取数重跑一遍，重跑却不会让它忽然有权限。
    """
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    save_report({**_SAMPLE, "news": {"status": "na"}, "missing": ["news"]})
    assert _is_latest_complete("20260904") is True


def test_is_latest_complete_requires_required_sections(tmp_path, monkeypatch) -> None:
    """**必需段**缺失仍要重试（默认 board/watchlist）。"""
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    old = datetime(2026, 1, 1, tzinfo=cn_tz()).isoformat(timespec="seconds")
    save_report({**_SAMPLE, "generated_at": old, "board": {"status": "na"}, "missing": ["board"]})
    assert _is_latest_complete("20260904") is False
    # 自定义必需段：把 news 也算必需 → na 的 news 就该触发重试
    save_report({**_SAMPLE, "generated_at": old, "news": {"status": "na"}, "missing": ["news"]})
    assert _is_latest_complete("20260904", ("board", "watchlist", "news")) is False


def test_is_latest_complete_retry_gap_blocks_hot_restart_loop(tmp_path, monkeypatch) -> None:
    """必需段缺失但刚生成过（15 分钟内）→ 先别重试。

    没有这层护栏，docker `restart: unless-stopped` 下的崩溃循环会变成对上游接口的高频刷取。
    """
    monkeypatch.setenv("QUICKREPORT_DIR", str(tmp_path))
    just_now = datetime.now(cn_tz()).isoformat(timespec="seconds")
    save_report({**_SAMPLE, "generated_at": just_now, "board": {"status": "na"}, "missing": ["board"]})
    assert _is_latest_complete("20260904") is True  # 护栏内：跳过

    long_ago = datetime(2026, 1, 1, tzinfo=cn_tz()).isoformat(timespec="seconds")
    save_report({**_SAMPLE, "generated_at": long_ago, "board": {"status": "na"}, "missing": ["board"]})
    assert _is_latest_complete("20260904") is False  # 护栏外：重试


def test_required_missing_reads_section_status_not_new_key(tmp_path) -> None:
    """从段 status 反算，不依赖任何新增的顶层键——旧存档没有那个键但 status 一直都在。"""
    from demomcp.quickreport.server import required_missing

    rep = {"board": {"status": "ok"}, "watchlist": {"status": "na"}}
    assert required_missing(rep, ("board", "watchlist")) == ["watchlist"]
    assert required_missing(None, ("board",)) == ["board"]
    assert required_missing({}, ("board",)) == ["board"]  # 段整个缺失也算 na


def test_next_run_dt_uses_fixed_utc8_fallback() -> None:
    """Windows 无 tzdata → cn_tz() 回退固定 UTC+8；next_run_dt 不触碰 ZoneInfo("UTC")。"""
    sched = Schedule(hour=8, minute=30)
    before = datetime(2026, 9, 7, 7, 0, tzinfo=cn_tz())
    assert next_run_dt(before, sched).date() == date(2026, 9, 7)
    after = datetime(2026, 9, 7, 9, 0, tzinfo=cn_tz())
    target = next_run_dt(after, sched)
    assert (target.hour, target.minute) == (8, 30)
    assert target.date() == date(2026, 9, 8)
