"""管理 REST 路由：per-source 状态查询 + 开关切换 + 整体健康度。与 /mcp 挂在同一个 Starlette/FastAPI
app、同一个端口（见 app.py），路径前缀 /admin，不走 MCP 协议——纯运维/前端设置页用的普通 JSON API。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/admin")


def _gateway(request: Request):
    return request.app.state.gateway


@router.get("/sources")
async def list_sources(request: Request) -> list[dict[str, Any]]:
    return await _gateway(request).status()


class SetSourceRequest(BaseModel):
    enabled: bool


@router.post("/sources/{source_id}")
async def set_source(source_id: str, req: SetSourceRequest, request: Request) -> dict[str, Any]:
    ok = await _gateway(request).set_enabled(source_id, req.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未知数据源：{source_id}")
    statuses = await _gateway(request).status()
    for s in statuses:
        if s["id"] == source_id:
            return s
    raise HTTPException(status_code=404, detail=f"未知数据源：{source_id}")  # pragma: no cover - 防御


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """健康 = 配置无问题 且 每个已启用的源都已连接；`config_problems` 直接把没配好的原因带给前端。"""
    statuses = await _gateway(request).status()
    problems: list[str] = list(getattr(request.app.state, "config_problems", []) or [])
    enabled = [s for s in statuses if s["enabled"]]
    connected_ok = all(s["connected"] for s in enabled) if enabled else True
    return {"ok": connected_ok and not problems, "config_problems": problems, "sources": statuses}
