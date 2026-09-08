"""快报结果持久化：`data/quickreport/latest.json` + 按日历史存档 `history/{YYYY-MM-DD}.json`。

读写逐字节镜像 curate.save_catalog/load_catalog；快报是派生缓存（重新生成即可重建），
不落 SQLAlchemy——避免 create_all 不 ALTER 的坑与三表语义不匹配。
- 默认 `save_report(report)` 同时写 latest.json（前端默认读）与 history/{date}.json（历史列表/回看）；
  显式传 `path`（测试隔离）时只写该处，`archive=False` 可跳过历史。
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from demomcp.quickreport.config import last_error_path, latest_path, quickreport_dir


def history_dir() -> Path:
    return quickreport_dir() / "history"


def _atomic_write(p: Path, report: dict[str, Any]) -> None:
    """临时文件 + os.replace：并发/中断下不产生半截 JSON。

    2026-09-07 审查：定时/手动并发生成时两个写者交错 open() 直写 latest.json，
    可能留下损坏文件（load_report → None → 前端「快报尚未生成」404）；
    os.replace 同目录原子换名，写者之间是 last-writer-wins 而非撕裂。
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def save_report(
    report: dict[str, Any], path: Path | str | None = None, *, archive: bool = True
) -> Path:
    """写 latest.json（幂等覆盖）；默认同时把该日报文存档进 history/{date}.json。返回 latest 路径。

    显式传 `path`（测试隔离）时，归档也写到该路径旁的 history/（原先归档恒写真实
    history_dir()——测试运行会把样本写进仓库 data/ 目录，2026-09-07 审查）。
    """
    p = Path(path) if path is not None else latest_path()
    _atomic_write(p, report)
    if archive:
        day = str(report.get("date") or "")
        if day:
            _atomic_write(p.parent / "history" / f"{day}.json", report)
    return p


def load_report(path: Path | str | None = None) -> dict[str, Any] | None:
    """读 latest.json；缺失/损坏/非 dict → None（调用方按「未生成」处理）。"""
    p = Path(path) if path is not None else latest_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_report_by_date(day: str) -> dict[str, Any] | None:
    """读某日的存档快报（day 为 ISO 日期 YYYY-MM-DD）；不存在/损坏 → None。"""
    p = history_dir() / f"{day}.json"
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_histories() -> list[dict[str, Any]]:
    """历史快报摘要列表（日期降序）：{date, generated_at, missing, status_ok}。"""
    out: list[dict[str, Any]] = []
    d = history_dir()
    if not d.is_dir():
        return []
    for f in sorted(d.glob("*.json"), reverse=True):
        data = load_report_by_date(f.stem)
        if data is None:
            continue
        missing = data.get("missing") if isinstance(data.get("missing"), list) else []
        out.append(
            {
                "date": f.stem,
                "generated_at": data.get("generated_at"),
                "missing": missing,
                "status_ok": len(missing) == 0,
            }
        )
    return out


def save_last_error(entry: dict[str, Any]) -> Path:
    """记录最近一次生成失败的状态（{at, error, stage}）；成功生成后可调用清空。"""
    p = last_error_path()
    _atomic_write(p, entry)
    return p


def load_last_error() -> dict[str, Any] | None:
    """读最近一次生成失败的记录（缺失/损坏 → None）。

    `save_last_error` 从加进来那天起就只有写没有读——状态看板要显示「上次为什么没跑成」，
    这半边必须补上，否则那个文件永远是个死产物。
    """
    p = last_error_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear_last_error() -> None:
    """成功生成后清掉失败记录——否则一次旧失败会永久挂在看板上。"""
    with contextlib.suppress(OSError):
        last_error_path().unlink(missing_ok=True)
