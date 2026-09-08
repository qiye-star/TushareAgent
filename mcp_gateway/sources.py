"""上游 MCP 源注册表：每条 SourceDef = {id, display_name, connect}；connect() 是零参 async context
manager 工厂，产出一个 demomcp 已有的 ToolProvider（providers/tools/mcp.py 的 MCPToolProvider /
providers/tools/wind.py 的 WindToolProvider）——不重新实现连接管理，直接复用其保活/断线重建。

扩展新源 = 在 build_sources() 里加一条；pool.py/admin.py/mcp_endpoint.py 全部按 source id 泛型处理，
不需要跟着改。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.providers.tools.mcp import mcp_tool_provider
from demomcp.providers.tools.wind import WindToolProvider
from mcp_gateway.config import GatewaySettings
from mcp_gateway.providers.ifind import IfindToolProvider


@dataclass(frozen=True)
class SourceDef:
    id: str
    display_name: str
    connect: Callable[[], object]  # 零参 -> AsyncContextManager[ToolProvider]
    default_enabled: bool = True


def build_sources(settings: GatewaySettings) -> list[SourceDef]:
    """按当前配置组出源列表；未配置的源不注册（不留一个永远连不上的开关项，见 config_problems()）。

    **顺序只决定展示与聚合顺序，不是优先级**：`pool.GatewayToolProvider` 的路由是「工具名 → 唯一源」，
    各源前缀互不重叠（`wind_*` / `ifind_*` / Tushare 原生名 / 免费源原生名），所以不存在「同名工具
    换源重试」这回事——跨源取舍发生在 agent 的选工具环节（某源报无权限时 LLM 改选另一源的等价接口），
    由提示词与 `graph/tool_select.py` 负责，不在网关里。
    """
    sources: list[SourceDef] = []
    if settings.tushare_configured:
        sources.append(
            SourceDef(
                id="tushare",
                display_name="Tushare 官方 MCP",
                connect=lambda: mcp_tool_provider(
                    settings.tushare_mcp_url,
                    timeout=settings.mcp_timeout,
                    retries=settings.mcp_retries,
                    keepalive_interval=settings.mcp_keepalive_interval or None,
                ),
            )
        )
    if settings.wind_configured:
        sources.append(
            SourceDef(
                id="wind",
                display_name="万得 Wind",
                connect=lambda: _wind_provider_cm(settings),
            )
        )
    if settings.ifind_configured:
        sources.append(
            SourceDef(
                id="ifind",
                display_name="同花顺 iFind",
                connect=lambda: _ifind_provider_cm(settings),
            )
        )
    # 免费源：各自跑在独立进程里（见 mcp_servers_ext/），网关只是它们的 MCP 客户端。
    # 与付费源的唯一区别就是「配的是 URL 而不是 key」——连接/保活/开关一视同仁。
    if settings.akshare_configured:
        sources.append(
            SourceDef(
                id="akshare",
                display_name="AkShare（免费）",
                connect=lambda: mcp_tool_provider(
                    settings.akshare_mcp_url,
                    timeout=settings.mcp_timeout,
                    retries=settings.mcp_retries,
                    keepalive_interval=settings.mcp_keepalive_interval or None,
                ),
            )
        )
    if settings.china_news_configured:
        sources.append(
            SourceDef(
                id="china_news",
                display_name="财经新闻聚合（免费）",
                connect=lambda: mcp_tool_provider(
                    settings.china_news_mcp_url,
                    timeout=settings.mcp_timeout,
                    retries=settings.mcp_retries,
                    keepalive_interval=settings.mcp_keepalive_interval or None,
                ),
            )
        )
    return sources


@asynccontextmanager
async def _wind_provider_cm(settings: GatewaySettings) -> AsyncIterator[ToolProvider]:
    async with WindToolProvider(
        settings.wind_api_key,
        timeout=settings.mcp_timeout,
        retries=settings.mcp_retries,
        keepalive_interval=settings.mcp_keepalive_interval or None,
    ) as wind:
        yield wind


@asynccontextmanager
async def _ifind_provider_cm(settings: GatewaySettings) -> AsyncIterator[ToolProvider]:
    """iFind 的并发上限走自己的配置项：远端按套餐硬限并发，超了直接被拒，不能沿用通用默认值。"""
    async with IfindToolProvider(
        settings.ifind_auth_token,
        timeout=settings.mcp_timeout,
        retries=settings.mcp_retries,
        max_concurrency=max(1, settings.ifind_concurrency),
        keepalive_interval=settings.mcp_keepalive_interval or None,
    ) as ifind:
        yield ifind
