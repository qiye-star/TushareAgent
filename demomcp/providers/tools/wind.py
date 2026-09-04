"""万得（Wind）MCP 工具来源：作为 MCP 客户端经 HTTP（streamable-http）连万得 7 个数据域。

每个数据域一个独立 MCP 端点（`https://mcp.wind.com.cn/vserver_<server_type>/mcp/`），鉴权用请求头
`Authorization: Bearer <WIND_API_KEY>`。本模块把各域工具统一加 `wind_` 前缀暴露（如 `wind_get_stock_quote`），
与 Tushare 的原始工具名区分；`call_tool` 剥前缀、按「原始名 → 域」索引路由到对应域复用一条 `ClientSession`。

`agent_tool_provider(settings)` 是唯一装配入口：`WIND_API_KEY` 生效时产出
`CompositeToolProvider([tushare, wind])`，否则退回单个 Tushare provider（行为不变）。
本模块只依赖 interfaces 与同层 `providers.tools.{mcp,composite}`，不触碰 `@mcp_server`。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from typing import Any, Self

from demomcp.config.settings import Settings
from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.composite import CompositeToolProvider
from demomcp.providers.tools.mcp import MCPToolProvider, mcp_tool_provider

PREFIX = "wind_"

# 源：wind-mcp-skill `scripts/cli.mjs` 的 SERVERS 映射（GitHub Wind-Information-Co-Ltd/wind-skills, main）。
WIND_DOMAINS: dict[str, str] = {
    "stock_data": "https://mcp.wind.com.cn/vserver_stock_data/mcp/",
    "fund_data": "https://mcp.wind.com.cn/vserver_fund_data/mcp/",
    "index_data": "https://mcp.wind.com.cn/vserver_index_data/mcp/",
    "bond_data": "https://mcp.wind.com.cn/vserver_bond_data/mcp/",
    "financial_docs": "https://mcp.wind.com.cn/vserver_financial_docs/mcp/",
    "economic_data": "https://mcp.wind.com.cn/vserver_economic_data/mcp/",
    "analytics_data": "https://mcp.wind.com.cn/vserver_analytics_data/mcp/",
}


class WindToolProvider:
    """把万得 7 个域合并成一个带 `wind_` 前缀的 ToolProvider；懒连接、单域失败隔离、永不抛。"""

    def __init__(
        self, api_key: str, *, timeout: float = 30.0, retries: int = 2, max_concurrency: int = 5
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._retries = retries
        self._max_concurrency = max_concurrency
        self._semaphore: asyncio.Semaphore | None = None
        self._stack: AsyncExitStack | None = None
        self._providers: dict[str, MCPToolProvider] = {}
        self._index: dict[str, str] = {}  # 原始工具名 → 域
        self._specs: list[ToolSpec] | None = None

    async def __aenter__(self) -> Self:
        self._stack = AsyncExitStack()
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    @asynccontextmanager
    async def _open(self, domain: str) -> AsyncIterator[MCPToolProvider]:
        """唯一的连接/鉴权点：每域一条 streamable-http 会话，复用 MCPToolProvider。"""
        endpoint = WIND_DOMAINS[domain]
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        async with mcp_tool_provider(
            endpoint, timeout=self._timeout, retries=self._retries, headers=headers
        ) as provider:
            yield provider

    async def _get_provider(self, domain: str) -> MCPToolProvider:
        if domain not in self._providers:
            assert self._stack is not None
            self._providers[domain] = await self._stack.enter_async_context(self._open(domain))
        return self._providers[domain]

    async def list_tools(self) -> list[ToolSpec]:
        if self._specs is None:
            specs: list[ToolSpec] = []
            index: dict[str, str] = {}
            for domain in WIND_DOMAINS:
                try:
                    provider = await self._get_provider(domain)
                    domain_specs = await provider.list_tools()
                except Exception as exc:  # noqa: BLE001 - 单域不可用不阻断，其余域照常暴露
                    print(f"[wind] 域 {domain} 不可用，跳过：{exc}")
                    continue
                for spec in domain_specs:
                    prefixed = replace(spec, name=f"{PREFIX}{spec.name}")
                    specs.append(prefixed)
                    index[spec.name] = domain
            self._specs = specs
            self._index = index
            _log_registered_wind(specs)
        return list(self._specs)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        raw = name[len(PREFIX) :] if name.startswith(PREFIX) else None
        domain = self._index.get(raw or "")
        if raw is None or domain is None:
            return ToolResult(content=f"Unknown wind tool: {name}", is_error=True)
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrency)
        try:
            async with self._semaphore:
                try:
                    provider = await self._get_provider(domain)
                except Exception as exc:  # noqa: BLE001 - 连接失败转 is_error，不崩图
                    return ToolResult(content=f"[wind] 连接域 {domain} 失败: {exc}", is_error=True)
                result = await provider.call_tool(raw, arguments)
        except Exception as exc:  # noqa: BLE001 - 调用异常转 is_error
            return ToolResult(content=f"[wind] 调用 {raw} 失败: {exc}", is_error=True)
        if result.is_error:
            return result
        signal = _wind_error_signal(result.content)
        if signal:
            return ToolResult(content=signal, is_error=True)
        return result


_WIND_RATE_LIMIT = "万得限流，请稍后再试或减少并发请求。"
_WIND_BACKEND = "万得后端返回错误，请稍后重试。"
_WIND_AUTH = "万得鉴权失败，请检查 WIND_API_KEY。"
_WIND_OUT_OF_SCOPE = "该查询超出万得支持范围。"
_WIND_VALIDATION = "万得参数校验失败："


def _wind_error_signal(text: str) -> str | None:
    """识别万得错误信封（ok:false 或非空 error 字段）→ 返回友好提示；否则 None（成功/非错误原样通过）。"""
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    code = ""
    msg = ""
    if body.get("ok") is False:
        code = str(body.get("code", "") or "")
        msg = str(body.get("message", "") or body.get("msg", "") or "")
    elif isinstance(body.get("error"), dict) and body["error"]:
        err = body["error"]
        code = str(err.get("code", "") or "")
        msg = str(err.get("message", "") or "")
    else:
        return None
    return _friendly_wind_error(code, msg)


def _friendly_wind_error(code: str, msg: str) -> str:
    if "RATE_LIMIT" in code or "限流" in msg:
        return _WIND_RATE_LIMIT
    if code == "backend_error" or "后端" in msg:
        return _WIND_BACKEND
    if "AUTH" in code:
        return _WIND_AUTH
    if "OUT_OF_SCOPE" in code:
        return _WIND_OUT_OF_SCOPE
    if "VALIDATION" in code:
        return _WIND_VALIDATION + (msg or "请核对参数")
    return f"万得调用返回错误：{msg or code or '未知'}。"


def _log_registered_wind(specs: list[ToolSpec]) -> None:
    """启动时打印一次已自动发现的万得工具清单（只打印，不改返回）。"""
    names = [s.name for s in specs]
    shown = ", ".join(names[:100]) + (" …" if len(names) > 100 else "")
    print(f"[wind] 已连接，可用工具 {len(names)} 个（{PREFIX} 前缀）：{shown}")


@asynccontextmanager
async def agent_tool_provider(settings: Settings) -> AsyncIterator[ToolProvider]:
    """装配 agent 要用的工具集：Tushare 必在；WIND_API_KEY 生效时并入万得（Composite），否则仅 Tushare。

    ```
    WIND_API_KEY 有效且 WIND_ENABLED=true  -> CompositeToolProvider([tushare, wind])
    否则                                    -> tushare（今日行为，逐字节一致）
    ```
    万得装配本身失败（如鉴权/网络异常）只退回 Tushare，绝不杀 agent。
    """
    async with mcp_tool_provider(
        settings.tushare_mcp_url, timeout=settings.mcp_timeout, retries=settings.mcp_retries
    ) as tushare:
        if not settings.wind_configured:
            yield tushare
            return
        try:
            async with WindToolProvider(
                settings.wind_api_key, timeout=settings.mcp_timeout, retries=settings.mcp_retries
            ) as wind:
                yield CompositeToolProvider([tushare, wind])
        except Exception as exc:  # noqa: BLE001 - Wind 装配失败仅退回 Tushare，保留主链路
            print(f"[wind] 装配失败，仅使用 Tushare：{exc}")
            yield tushare
