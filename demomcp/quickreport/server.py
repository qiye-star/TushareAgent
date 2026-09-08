"""web 端点薄壳：取数 + 计算 + 落盘，一个函数出全套（镜像 rag/server.py「纯函数 + 端点薄壳」定位）。

`generate_from` 是唯一入口：web 端点 / 独立脚本 / 定时任务共用同一实现；
`ConfigError`（watchlist 配置缺失/损坏）**不在这里 catch**——由调用方决定 422 / 记错跳过。
"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.quickreport.config import ConfigError, WatchlistConfig, watchlist_path
from demomcp.quickreport.pipeline import build_report
from demomcp.quickreport.store import clear_last_error, load_report, save_report


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
        # 成功生成就清掉旧的失败记录，否则一次陈年失败会永久挂在状态看板上
        clear_last_error()
    return report


def load_latest() -> dict[str, Any] | None:
    """读最近一次成功生成的快报（未生成/损坏 → None）。"""
    return load_report()


SECTION_NAMES: tuple[str, ...] = ("board", "watchlist", "announce", "forecast", "news")


def status_from(report: dict[str, Any] | None) -> dict[str, Any]:
    """健康行摘要（供 /api/quickreport/status 与调试复用）。

    `None` → 恰好 `{"exists": False}`（被 test_quickreport_store.py 锁定，别加字段）。
    非 None 分支带上各段 provenance，让前端来源看板能显示「这一段由哪个源、哪个工具服务」。
    """
    if report is None:
        return {"exists": False}
    g = report.get("generated_at") or ""
    try:
        hhmm = str(g).split("T")[1][:5]  # HH:MM
    except IndexError:
        hhmm = ""
    sections: dict[str, Any] = {}
    for name in SECTION_NAMES:
        sec = report.get(name)
        if not isinstance(sec, dict):
            continue
        rows = sec.get("rows") if isinstance(sec.get("rows"), list) else sec.get("items")
        sections[name] = {
            "status": sec.get("status"),
            "src": sec.get("src"),
            "src_tool": sec.get("src_tool"),
            "src_label": sec.get("src_label"),
            "note": sec.get("note"),
            "count": len(rows) if isinstance(rows, list) else None,
            "fetched_at": sec.get("fetched_at"),
            "attempts": sec.get("attempts", []),
        }
    errors = report.get("errors") if isinstance(report.get("errors"), list) else []
    by_source: dict[str, int] = {}
    for e in errors:
        if isinstance(e, dict):
            key = str(e.get("source") or "unknown")
            by_source[key] = by_source.get(key, 0) + 1
    return {
        "exists": True,
        "generated_at": report.get("generated_at"),
        "date": report.get("date"),
        "missing": report.get("missing", []),
        "generated_hhmm": hhmm,
        "sector": report.get("sector"),
        "version": report.get("version"),
        "sections": sections,
        "errors_count": len(errors),
        "errors_by_source": by_source,
        "series_status": (report.get("board") or {}).get("series_status")
        if isinstance(report.get("board"), dict)
        else None,
    }


def required_missing(report: dict[str, Any] | None, required: tuple[str, ...]) -> list[str]:
    """`required` 里 status 为 na 的段名。

    **从段 status 反算**而不是读一个新增的顶层键：旧的 latest.json / history 存档里
    没有那个键，但五段的 status 一直都在——向后兼容零迁移。
    """
    if not report:
        return list(required)
    out: list[str] = []
    for name in required:
        sec = report.get(name)
        status = str((sec or {}).get("status") or "na") if isinstance(sec, dict) else "na"
        if status == "na":
            out.append(name)
    return out
