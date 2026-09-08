"""每日定时生成（sleep-until 模式，与 web.py `_recycle_tools_loop` 同构）：

- `next_run_dt`：全程 `cn_tz()` 计算（容器常跑 UTC，直接 `datetime.now()` 会让 08:30 漂到 00:30 UTC）；
  UTC 时刻用 stdlib `timezone.utc`——Windows 无 tzdata，`ZoneInfo("UTC")` 直接抛
  （2026-09-07 审查：调度器首轮循环死于 `ZoneInfoNotFoundError`，每日任务静默停机）；
- `run_forever(get_tools)`：进入循环前补跑一次（latest 缺失或日期过期时，给工具池 30s 宽限让热启动先跑），
  之后 sleep 到下一触发时刻 → `_run_once`；
- `_run_once`：每次重新加载 watchlist.json（改配置不重启即生效）；去重守卫 `date == 预期日` 跳过
  （周末/假日无新数据，上一份就是最新），**且缺段的报告不算最新**——部分降级（missing 非空）
  会在下次触发（如进程重启后的补跑）时重试，而不是整天停在「未接入」退化态；
- `lock`（web 传入 app.state.quickreport_lock）与手动生成端点共用一把锁：定时/手动不再并发生成；
- 循环内**不吞 CancelledError**（透传由 lifespan 取消收尾）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from demomcp.quickreport.config import ConfigError, Schedule, WatchlistConfig, cn_tz
from demomcp.quickreport.store import load_report, save_last_error

log = logging.getLogger("quickreport")

_WARMUP = 30.0  # 启动补跑前的宽限：给工具池热启动让路


def _tz_of(sched: Schedule):
    """调度时区：sched.tz 优先；数据库缺失（Windows）回退 UTC+8。"""
    if sched.tz == "Asia/Shanghai":
        return cn_tz()
    try:
        return ZoneInfo(sched.tz or "Asia/Shanghai")
    except Exception:  # noqa: BLE001 - tz 数据库缺失 → 回退 UTC+8（快报日程只配中国时区）
        return cn_tz()


def next_run_dt(now: datetime, sched: Schedule) -> datetime:
    """now（任意时区 aware）之后的下一个触发时刻（返回 aware Asia/Shanghai）。"""
    tz = _tz_of(sched)
    now_at = now.astimezone(tz)
    candidate = now_at.replace(hour=sched.hour, minute=sched.minute, second=0, microsecond=0)
    if candidate <= now_at:
        candidate += timedelta(days=1)
    return candidate


async def run_forever(
    get_tools: Callable[[], Awaitable[Any]],
    *,
    stage_timeout: float = 60.0,
    concurrency: int = 4,
    lock: asyncio.Lock | None = None,
    release_tools: Callable[[Any], Awaitable[None]] | None = None,
) -> None:
    """每日调度主循环：启动补跑 → sleep-until → _run_once → 循环。

    get_tools 由调用方注入（web 传 `lambda: _get_tools(app)`，测试传假 provider 工厂）；
    lock 与手动端点共用（生成互斥，2026-09-07 审查：原先定时路径不持锁，可与手动生成并发写坏 latest.json）；
    release_tools 在单轮结束时归还工具池租约（web 传 `lambda t: _release_tools(app, t)`）。
    """
    from demomcp.quickreport.server import generate_from

    # 启动补跑：latest 缺失或过期时立即生成一份（_run_once 自带去重守卫，安全幂等）；
    # 短宽限给工具池热启动让路；补跑失败不阻断调度循环
    try:
        await asyncio.sleep(min(_WARMUP, 5.0))
        await _run_once(get_tools, generate_from, stage_timeout, concurrency, lock=lock, release_tools=release_tools)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("快报启动补跑失败：%s", exc)

    while True:
        cfg = WatchlistConfig.load()
        if cfg is None:
            log.warning("watchlist 配置缺失，等待重试（30 分钟）")
            await asyncio.sleep(1800.0)
            continue
        target = next_run_dt(datetime.now(UTC), cfg.schedule)
        now_cn = datetime.now(cn_tz())
        log.info("快报调度：下次生成 %s（本地 %s）", target.isoformat(), now_cn.isoformat(timespec="seconds"))
        await asyncio.sleep(max(0.0, (target - datetime.now(UTC)).total_seconds()))
        try:
            await _run_once(get_tools, generate_from, stage_timeout, concurrency, lock=lock, release_tools=release_tools)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单轮失败保留旧 latest.json + last_error.json
            log.warning("快报定时生成失败：%s", exc)
            save_last_error({"at": datetime.now(cn_tz()).isoformat(timespec="seconds"), "error": str(exc)})


async def _run_once(
    get_tools,
    generate_from,
    stage_timeout: float,
    concurrency: int,
    *,
    lock: asyncio.Lock | None = None,
    release_tools: Callable[[Any], Awaitable[None]] | None = None,
) -> None:
    """单轮生成：最新配置 + 最近引用工具池；去重守卫后生成落盘。"""
    cfg = WatchlistConfig.load()
    if cfg is None:
        raise ConfigError("watchlist 配置缺失或损坏（data/quickreport/watchlist.json）")
    tools = await get_tools()
    try:
        from demomcp.quickreport.pipeline import _Ctx, resolve_report_date

        ctx = _Ctx(asyncio.Semaphore(concurrency), stage_timeout)
        expected = await resolve_report_date(tools, ctx, None)
        if _is_latest_complete(expected):
            log.info("快报已是最新（%s），跳过生成", expected)
            return
        # 与手动端点共用同一把锁：持锁期间对方可能刚生成完 → 锁内复查
        async with lock if lock is not None else nullcontext():
            if _is_latest_complete(expected):
                log.info("快报已是最新（%s），跳过生成", expected)
                return
            report = await generate_from(tools, cfg, None, stage_timeout=stage_timeout, concurrency=concurrency)
        log.info(
            "快报已生成：%s missing=%s errors=%d",
            report.get("date"), report.get("missing"), len(report.get("errors", [])),
        )
    finally:
        if release_tools is not None:
            await release_tools(tools)


def _is_latest_complete(expected: str) -> bool:
    """去重守卫：最新报告的日期 == 预期日 **且无缺段**（missing 非空 = 部分降级，不算最新，下次触发重试）。"""
    latest = load_report()
    if not latest or str(latest.get("date", "")).replace("-", "") != expected:
        return False
    return not (latest.get("missing") or [])
