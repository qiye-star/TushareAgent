"""按源持久化开关：<data_dir>/sources.json {"<id>": {"enabled": bool}}。

原子写（临时文件 + os.replace），镜像 demomcp/config/mcp_toggle.py 的手法，泛化成按 id 的字典；
未知 id（文件里没有的）由调用方用 SourceDef.default_enabled 兜底，不是这里的职责。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def sources_toggle_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "sources.json"


def load_toggle_state(data_dir: str | Path) -> dict[str, bool]:
    """文件不存在/损坏/字段类型不对 → {}（不是某个 id 的默认值，是「完全没有记录」）。"""
    p = sources_toggle_path(data_dir)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, bool] = {}
    for k, v in data.items():
        if isinstance(v, dict) and "enabled" in v:
            out[str(k)] = bool(v["enabled"])
    return out


def save_source_enabled(data_dir: str | Path, source_id: str, enabled: bool) -> None:
    """更新单个 source 的开关并整体原子写回（读-改-写；小文件、低频操作，串行调用足够安全）。"""
    p = sources_toggle_path(data_dir)
    state: dict[str, Any] = {}
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            state = raw
    except (OSError, ValueError):
        state = {}
    state[source_id] = {"enabled": bool(enabled)}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
