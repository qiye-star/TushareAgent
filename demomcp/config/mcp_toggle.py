"""MCP 全局运行时开关：持久化在 data/settings/mcp_toggle.json，跨进程重启保留。

与 quickreport/config.py 的 watchlist.json 同一套读写哲学，但语义相反——这里核心字段缺失/文件
不存在时**不是** fail-fast，而是回退默认 True（保留改动前的行为：不写这个开关等价于「一直开着」）。
写入走 quickreport/store.py 同款临时文件 + os.replace 原子换名，避免并发写出半截 JSON。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from demomcp.config.env import PROJECT_ROOT


def mcp_toggle_path() -> Path:
    return PROJECT_ROOT / "data" / "settings" / "mcp_toggle.json"


def load_mcp_enabled(path: Path | str | None = None) -> bool:
    """读开关状态；文件不存在/损坏/字段类型不对 → 默认 True（不回归现有行为）。"""
    p = Path(path) if path is not None else mcp_toggle_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    return bool(data.get("enabled", True))


def save_mcp_enabled(enabled: bool, path: Path | str | None = None) -> None:
    """原子写 {"enabled": bool, "updated_at": iso8601}。"""
    p = Path(path) if path is not None else mcp_toggle_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"enabled": bool(enabled), "updated_at": datetime.now(UTC).isoformat()}
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
