"""MCP 工具来源：作为 MCP 客户端经 HTTP（streamable-http）连接独立 mcp_server，把 FastMCP 工具直译为中立 ToolSpec。

整个会话复用一条 MCP 连接。工具返回 {code,msg,row_count,data} 是 HTTP 200 的正常字典，
code!=0 视为业务失败；调用出错（异常或业务失败）会重试几次，若最终是权限类失败则在结果里明确
提示「积分不足/需更高积分」，由 LLM 如实转述给用户（而非假装取到数）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool as MCPTool

from demomcp.interfaces.types import ToolResult, ToolSpec


def to_tool_spec(tool: MCPTool) -> ToolSpec:
    """把 mcp.types.Tool 直译为中立 ToolSpec（name / description / inputSchema）。"""
    return ToolSpec(name=tool.name, description=tool.description or "", input_schema=tool.inputSchema)


class MCPToolProvider:
    def __init__(self, session: Any, *, timeout: float = 30.0, retries: int = 2) -> None:
        # session 是 mcp.ClientSession；用 Any 是为了测试注入鸭子类型的假 session，
        # 同时真 ClientSession（mcp_client 工厂）也满足。
        self._session = session
        self._timeout = timeout
        self._retries = retries

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._session.list_tools()
        specs = [to_tool_spec(t) for t in result.tools]
        _log_registered_tools(specs)
        return specs

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """调用工具：带读超时 + 重试。

        重试『抛出的异常』与『业务失败』（isError 或返回 JSON 的 code!=0）；重试耗尽后：
          - 权限类失败（msg 含 积分/权限/无权限/提升 等）→ 返回友好提示（is_error=False），让 LLM 如实转述；
          - 其它业务失败 → 保留原始结果；
          - 异常耗尽 → 返回带 "after N tries" 的错误信息（is_error=True）。
        """
        args = arguments or {}
        last_exc: Exception | None = None
        last_text: str | None = None
        last_was_error: bool = False
        last_permission: bool = False
        for attempt in range(self._retries):
            try:
                result = await self._session.call_tool(
                    name, args, read_timeout_seconds=timedelta(seconds=self._timeout)
                )
            except Exception as exc:  # noqa: BLE001 - 工具层吞掉一切，转 is_error 保循环存活
                last_exc = exc
                last_text = None
                last_was_error = True
                last_permission = False
                if attempt < self._retries - 1:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                return ToolResult(
                    content=f"Error calling {name} after {self._retries} tries: {exc}", is_error=True
                )
            parts = [b.text for b in result.content if getattr(b, "type", None) == "text"]
            text = "\n".join(parts) or "OK"
            # 成功：既非 isError 也非业务 code!=0
            if not result.isError and not _is_business_error(text):
                return ToolResult(content=text, is_error=False)
            # 业务失败：记录并退避后重试
            last_text = text
            last_was_error = bool(result.isError)
            last_permission, _msg = _permission_signal(text)
            if attempt < self._retries - 1:
                await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                continue
            # 重试耗尽
            if last_permission:
                return ToolResult(
                    content=(
                        f"调用 {name} 失败：该接口需更高积分或当前账号无权限"
                        f"（代理提示：{_short_msg(last_text)}）。请告知用户积分不足，或改用其它接口。"
                    ),
                    is_error=False,
                )
            return ToolResult(content=last_text or "OK", is_error=last_was_error)
        # 理论不可达：循环内总会返回
        return ToolResult(
            content=f"Error calling {name} after {self._retries} tries: {last_exc}", is_error=True
        )


def _parse_result(text: str) -> dict[str, Any] | None:
    """尝试把工具返回文本解析成 JSON 对象（代理返回 {code,msg,...}）；非对象返回 None。"""
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    return body if isinstance(body, dict) else None


def _is_business_error(text: str) -> bool:
    """返回内容是否为业务失败：解析出 JSON 且 code 非 0。"""
    body = _parse_result(text)
    if body is None:
        return False
    code = body.get("code", 0)
    return isinstance(code, (int, float)) and code != 0


def _permission_signal(text: str) -> tuple[bool, str]:
    """探测内容是否指向积分/权限不足；返回 (是否权限类, 归一化 msg)。"""
    body = _parse_result(text)
    msg = str(body.get("msg", "")) if body and body.get("msg") else (text or "")
    keywords = ("积分", "权限", "无权限", "提升", "需提高", "需提升", "points")
    return any(k in msg for k in keywords), msg


def _short_msg(text: str | None) -> str:
    """截取代理 msg 用于一句话提示，避免把整块 JSON 塞给 LLM。"""
    if not text:
        return "接口无权限"
    body = _parse_result(text)
    if body:
        return str(body.get("msg") or text)[:120]
    return text[:120]


def _log_registered_tools(specs: list[ToolSpec]) -> None:
    """启动时打印一次已自动发现的工具清单，便于排查（只打印，不改返回）。"""
    names = [s.name for s in specs]
    shown = ", ".join(names[:100]) + (" …" if len(names) > 100 else "")
    print(f"[mcp] 已连接，可用工具 {len(names)} 个：{shown}")


@asynccontextmanager
async def mcp_tool_provider(
    url: str, *, timeout: float = 30.0, retries: int = 2
) -> AsyncIterator[MCPToolProvider]:
    """经 HTTP 连接独立 mcp_server 并复用一条 MCP 连接，整个 asyncio 会话内有效。"""
    async with streamablehttp_client(url) as (read, write, _session_id), ClientSession(read, write) as session:
        await session.initialize()
        yield MCPToolProvider(session, timeout=timeout, retries=retries)
