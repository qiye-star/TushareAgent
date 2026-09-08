"""报告 skill 的按技能开关：持久化在 data/settings/skills.json，跨进程重启保留。

与 `mcp_toggle.py` / `mcp_gateway/toggle_store.py` 同一套读写哲学：
- 文件不存在 / 损坏 / 字段类型不对 → 视为「完全没有记录」，由调用方按默认（启用）兜底；
- 原子写（临时文件 + `os.replace`），避免并发写出半截 JSON；
- 写失败只记 WARNING，不让一次开关操作把请求打崩。

生效点在 `graph/skills.py::build_router_system`（每次 router 现读）→ 改开关**无需重启**即生效。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demomcp.config.env import PROJECT_ROOT

_log = logging.getLogger(__name__)


def skill_toggle_path() -> Path:
    return PROJECT_ROOT / "data" / "settings" / "skills.json"


def load_skill_toggles(path: Path | str | None = None) -> dict[str, bool]:
    """返回**显式记录过**的 id → enabled；没有记录的 id 不在字典里（调用方按默认启用处理）。"""
    p = Path(path) if path is not None else skill_toggle_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("skills") if isinstance(data.get("skills"), dict) else data
    out: dict[str, bool] = {}
    for k, v in raw.items():
        if isinstance(v, bool):
            out[str(k)] = v
        elif isinstance(v, dict) and "enabled" in v:
            out[str(k)] = bool(v["enabled"])
    return out


def is_skill_enabled(skill_id: str, path: Path | str | None = None) -> bool:
    """未记录 → True（默认全部启用，与「不写开关等价于一直开着」一致）。"""
    return load_skill_toggles(path).get(skill_id, True)


def save_skill_enabled(skill_id: str, enabled: bool, path: Path | str | None = None) -> dict[str, bool]:
    """读-改-写单个技能开关（小文件、低频操作），返回写入后的完整状态。"""
    p = Path(path) if path is not None else skill_toggle_path()
    state = load_skill_toggles(p)
    state[skill_id] = bool(enabled)
    body: dict[str, Any] = {"skills": state, "updated_at": datetime.now(UTC).isoformat()}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError as exc:  # 落盘失败不该让一次开关请求 500——内存状态已返回，重启后回到默认
        _log.warning("skill toggle 落盘失败 %s：%s", p, exc)
    return state
