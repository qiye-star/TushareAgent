"""web 端点薄壳：取数 + 计算 + 落盘，一个函数出全套（镜像 rag/server.py「纯函数 + 端点薄壳」定位）。

`generate_from` 是唯一入口：web 端点 / 独立脚本 / 定时任务共用同一实现；
`ConfigError`（watchlist 配置缺失/损坏）**不在这里 catch**——由调用方决定 422 / 记错跳过。
"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.quickreport.config import ConfigError, WatchlistConfig, watchlist_path
from demomcp.quickreport.pipeline import build_report
from demomcp.quickreport.store import load_report, save_report


async def generate_from(
    tools: ToolProvider,
    cfg: WatchlistConfig | None = None,
    report_date: str | None = None,
    *,
    stage_timeout: float = 60.0,
    concurrency: int = 4,
    save: bool = True,
) -> dict[str, Any]:
    """装配完整生成流程：解析报告日期 → 取数计算 → （可选）落盘 latest.json → 返回报告 dict。

    配置缺失/损坏 → 抛 ConfigError；其余任何失败都在报告中归一（status/errors），绝不裸抛。
    """
    if cfg is None:
        cfg = WatchlistConfig.load(watchlist_path())
        if cfg is None:
            raise ConfigError("watchlist 配置缺失或损坏（data/quickreport/watchlist.json）")
    report = await build_report(
        tools, cfg, report_date, stage_timeout=stage_timeout, concurrency=concurrency
    )
    if save:
        save_report(report)
    return report


def load_latest() -> dict[str, Any] | None:
    """读最近一次成功生成的快报（未生成/损坏 → None）。"""
    return load_report()


def status_from(report: dict[str, Any] | None) -> dict[str, Any]:
    """健康行摘要（供 /api/quickreport/latest 与调试复用）。"""
    if report is None:
        return {"exists": False}
    g = report.get("generated_at") or ""
    try:
        g = str(g).split("T")[1][:5]  # HH:MM
    except IndexError:
        g = ""
    return {
        "exists": True,
        "generated_at": report.get("generated_at"),
        "date": report.get("date"),
        "missing": report.get("missing", []),
        "generated_hhmm": g,
    }
