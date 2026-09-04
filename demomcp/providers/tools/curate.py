"""工具可用性探测（默认关、按需开启）：逐个接口真实调用一次，按 code/msg 分桶 usable/blocked/down。

- 只依赖 interfaces 与被注入的 provider；`probe_availability` 全 try/except，任何失败只影响缓存、永不阻塞。
- 结果为 `{tool_name: {"status": ..., "msg": ...}}`，经 `save_catalog` 落 JSON（TOOL_PROBE_CACHE_PATH）；
  `Agent` 有缓存时用 `select_tools(... catalog=...)` 剔除 blocked/down，meta 工具恒保留。
- 权威接口清单/参数在注册表（/api/registry）；本模块仅按已被发现的 tool 逐个探测，不做元数据假设。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from demomcp.interfaces.types import META_TOOL_NAMES, ToolResult, ToolSpec

# 权限/积分不足 → blocked（会被剔除）；接口下线/未订阅 → down（剔除）；其余（含缺参、正常空数据）→ usable（保留）。
_PERMISSION = ("积分", "权限", "无权限", "提升", "需提高", "需提升", "points", "积分不足")
_DOWN = ("下线", "未订阅", "停用", "下架", "订阅", "权限不足", "积分不足")


def _parse(text: str) -> dict[str, Any] | None:
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def _bucket(msg: str) -> str:
    """按 msg 关键词分桶；默认 usable（宁可保留，不误杀）。"""
    if any(k in msg for k in _PERMISSION):
        return "blocked"
    if any(k in msg for k in _DOWN):
        return "down"
    return "usable"


async def _classify(provider: Any, spec: ToolSpec, sample_params: dict[str, Any] | None) -> tuple[str, str]:
    """对单个接口探测：返回 (status, short_msg)。异常保守返回 usable，避免误杀。"""
    try:
        tr: ToolResult = await provider.call_tool(spec.name, dict(sample_params or {}))
    except Exception as exc:  # noqa: BLE001 - 探测异常不判死，保留
        return "usable", f"异常：{type(exc).__name__}"
    text = tr.content or ""
    body = _parse(text)
    if isinstance(body, dict):
        code = body.get("code", 0)
        if isinstance(code, (int, float)) and code != 0:
            msg = str(body.get("msg") or text or "")
            return _bucket(msg), msg[:120]
        return "usable", (str(body.get("msg") or "") or "ok")[:120]
    if tr.is_error:
        return _bucket(text), text[:120]
    return "usable", "ok"


async def probe_availability(
    provider: Any,
    specs: list[ToolSpec],
    *,
    concurrency: int = 4,
    sample_params: dict[str, Any] | None = None,
    on_progress=None,
) -> dict[str, dict[str, str]]:
    """逐个接口探测可用性；返回 `{name: {status, msg}}`（meta 工具不探测、跳过）。

    concurrency 限并发（避免瞬时打满积分/限流）；on_progress(name, status) 可选回调。
    """
    meta = META_TOOL_NAMES
    todo = [s for s in specs if s.name not in meta]
    sem = asyncio.Semaphore(max(1, concurrency))
    results: dict[str, dict[str, str]] = {}

    async def one(spec: ToolSpec) -> None:
        async with sem:
            status, msg = await _classify(provider, spec, sample_params)
        results[spec.name] = {"status": status, "msg": msg}
        if on_progress:
            on_progress(spec.name, status)

    await asyncio.gather(*(one(s) for s in todo))
    return results


def save_catalog(catalog: dict[str, dict[str, str]], path: str) -> None:
    """落盘探测结果；path 为空则跳过。"""
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)


def load_catalog(path: str) -> dict[str, Any] | None:
    """读可用性缓存；缺失/损坏 → None（不剔除，行为同今日）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None
