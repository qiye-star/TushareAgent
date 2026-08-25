"""Tushare 数据代理 MCP Server（demo-mcp 内置）。

独立进程、经 HTTP 复用 Tushare 数据代理的缓存/鉴权/错误提示，把下列工具暴露给 demo-mcp agent。
本模块只依赖 mcp + httpx；把 TUSHARE_* 环境变量看成外部配置，不 import 本包其它模块。

环境变量：
    TUSHARE_PROXY_URL        代理地址（默认 http://127.0.0.1:8000）
    TUSHARE_API_KEY          代理分发的 api_key（空则 query 返回 HTTP 401→ProxyError）
    TUSHARE_PROXY_TIMEOUT    每次请求超时秒数（默认 20）

注意：MCP SDK 新版 2.x 已移除 mcp.server.fastmcp.FastMCP，故依赖钉在 mcp>=1.28,<2。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

PROXY_URL = os.environ.get("TUSHARE_PROXY_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.environ.get("TUSHARE_API_KEY", "").strip()
TIMEOUT = float(os.environ.get("TUSHARE_PROXY_TIMEOUT", "20"))

mcp = FastMCP("tushare-proxy")


class ProxyError(RuntimeError):
    """代理不可达 / 未配置 Key / 查询失败的统一异常；message 面向 LLM 可读。"""


# ---------------------------------------------------------------------------
# 可脱离 MCP 直接调用的核心逻辑（测试只需注入 httpx.AsyncClient）。
# ---------------------------------------------------------------------------
async def do_list_apis(client: httpx.AsyncClient, q: str, limit: int) -> list[dict[str, Any]]:
    """GET /api/registry → list[{api_name,title,path,params,columns}]，无需鉴权。"""
    resp = await client.get("/api/registry", params={"q": q, "limit": limit})
    resp.raise_for_status()
    return resp.json()


async def do_get_api_info(client: httpx.AsyncClient, api_name: str) -> dict[str, Any]:
    """GET /api/registry?q=<api_name> 后精确匹配；找不到返回 {'api_name', 'found': False}。"""
    resp = await client.get("/api/registry", params={"q": api_name, "limit": 50})
    resp.raise_for_status()
    for entry in resp.json():
        if entry["api_name"] == api_name:
            return entry
    return {"api_name": api_name, "found": False}


async def do_query(
    client: httpx.AsyncClient,
    api_key: str,
    api_name: str,
    params: dict[str, Any],
    fields: str | None = None,
) -> dict[str, Any]:
    """POST /api/query；把代理的 {fields,items} 转成 list[dict]（LLM 更易读）。

    HTTP 200 + code!=0 → 原样返回 {code,msg,row_count:0,data:[]}（代理的友好 msg）。
    HTTP 401 → raise ProxyError（Key 未配置/无效）。
    连接异常 → raise ProxyError（代理没启动）。
    """
    payload = {"api_name": api_name, "token": api_key, "params": params, "fields": fields or ""}
    try:
        resp = await client.post("/api/query", json=payload)
    except httpx.TransportError as exc:
        raise ProxyError(f"MCP 代理不可达（{PROXY_URL}）：{exc}") from exc

    if resp.status_code == 401:
        raise ProxyError(
            "TUSHARE_API_KEY 未配置或无效（HTTP 401）。请到 /keys 页面（admin/admin123 登录）"
            "新建并导出 API Key，配置环境变量后再试。"
        )
    if resp.status_code >= 400:
        raise ProxyError(f"MCP 代理返回异常状态：HTTP {resp.status_code}")

    body = resp.json()
    code = int(body.get("code", -1))
    msg = body.get("msg", "")
    if code != 0:
        return {"code": code, "msg": msg, "row_count": 0, "data": []}

    fields_list = body["data"]["fields"]
    items = body["data"]["items"]
    rows = [dict(zip(fields_list, row)) for row in items]
    return {"code": 0, "msg": msg, "row_count": len(rows), "data": rows}


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=PROXY_URL, timeout=TIMEOUT)


# ---------------------------------------------------------------------------
# FastMCP 工具
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_apis(q: str = "", limit: int = 30) -> list[dict[str, Any]]:
    """列出可用的 Tushare 数据接口（注册表）。

    适用场景：不确定该用哪个接口取数、想浏览全部接口，或按关键词搜索可用接口时使用。
    q 可按接口名 / api_name / 标题 / 路径做模糊搜索；limit 控制返回条数（默认 30，
    需要全部 226 个接口时可调大）。返回 list[{api_name, title, path, params, columns}]。
    拿到候选后请用 get_api_info 看某个接口的必填参数。
    """
    async with _new_client() as client:
        try:
            return await do_list_apis(client, q, limit)
        except httpx.HTTPError as exc:
            raise ProxyError(f"MCP 代理不可达或响应异常（{PROXY_URL}）：{exc}") from exc


@mcp.tool()
async def get_api_info(api_name: str) -> dict[str, Any]:
    """查看单个 Tushare 接口的详情（必填/可选参数及含义、返回字段）。

    适用场景：list_apis 已缩小候选范围后，需要确认某个接口的入参（哪些 required）和返回列时使用。
    返回 {api_name, title, path, params:{参数名:{type,required,description}}, columns:[...]}；
    若接口不存在则返回 {api_name, found: False}。
    """
    async with _new_client() as client:
        try:
            return await do_get_api_info(client, api_name)
        except httpx.HTTPError as exc:
            raise ProxyError(f"MCP 代理不可达或响应异常（{PROXY_URL}）：{exc}") from exc


@mcp.tool()
async def query(
    api_name: str,
    params: dict[str, Any] | None = None,
    fields: str | None = None,
) -> dict[str, Any]:
    """从 Tushare（经本地缓存代理）取数。

    适用场景：已确定接口名和入参后拉取具体数据。params 必须是该接口合法的参数
    （见 get_api_info 的 required）；fields 传逗号分隔的列名可只返回部分列，留空返回全部列。
    返回 {code, msg, row_count, data:[{字段:值}...]}；code!=0 时 data 为空、msg 为代理返回的
    友好提示（可能是"提高积分/接口下线/api_key 未配置"等）。token 由 MCP 进程的环境变量
    TUSHARE_API_KEY 提供，无需在 params 里传。
    """
    async with _new_client() as client:
        try:
            return await do_query(client, API_KEY, api_name, params or {}, fields)
        except httpx.HTTPError as exc:
            raise ProxyError(f"MCP 代理不可达或响应异常（{PROXY_URL}）：{exc}") from exc


if __name__ == "__main__":
    mcp.run()  # 默认 stdio 传输
