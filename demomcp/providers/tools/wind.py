"""万得（Wind）MCP 工具来源：作为 MCP 客户端经 HTTP（streamable-http）连万得 7 个数据域。

每个数据域一个独立 MCP 端点（`https://mcp.wind.com.cn/vserver_<server_type>/mcp/`），鉴权用请求头
`Authorization: Bearer <WIND_API_KEY>`。本模块内部仍把各域工具统一加 `wind_` 前缀索引（如
`wind_get_stock_quote`），但**对外只暴露 3 个合成元工具**——`wind_list_apis`/`wind_get_api_info`/
`wind_query`，镜像 Tushare 官方 MCP 已有的 `list_apis`/`get_api_info`/`query` 懒发现三件套（也
呼应万得自家 skill.md 推荐的「按 server_type 分域、按名分发调用」而非一次性摊开全部 schema 的做法）：
LLM 先 `wind_list_apis` 浏览、`wind_get_api_info` 确认某接口参数，再 `wind_query(api_name, params)`
真正调用——`wind_query` 内部直接复用 `_dispatch`（原直连路由/限流/错误归一化逻辑，一行未改）。

装配方（谁来 new 它）已上移到 **MCP 网关**：`mcp_gateway/sources.py` 把本类作为 `wind` 源注册、
`mcp_gateway/pool.py` 负责按源开关与跨源聚合。demomcp 应用进程不再直连万得（也不再持有 WIND_API_KEY），
本模块对它而言只是一份被网关复用的实现。只依赖 interfaces 与同层 `providers.tools.mcp`，不触碰 `@mcp_server`。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from typing import Any, Self

from demomcp.interfaces.types import (
    WIND_GET_API_INFO,
    WIND_LIST_APIS,
    WIND_QUERY,
    ToolResult,
    ToolSpec,
)
from demomcp.providers.tools.mcp import MCPToolProvider, mcp_tool_provider

PREFIX = "wind_"

# 对外暴露的万得懒发现三件套；具体的 35 个 wind_* schema 只在内部 _specs/_by_name 缓存，
# 经 wind_list_apis/wind_get_api_info 按需查询，不进 llm.chat(tools=...) 的每轮载荷。
_WIND_META_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name=WIND_LIST_APIS,
        description="浏览万得(Wind)可用接口名与简介，可按关键词过滤（如 行情/财务/公告/新闻/宏观/基金/债券）。"
        "拿不准该用哪个万得接口时先调用这个。",
        input_schema={
            "type": "object",
            "properties": {"keyword": {"type": "string", "description": "可选关键词，按名字/简介子串过滤"}},
        },
    ),
    ToolSpec(
        name=WIND_GET_API_INFO,
        description="查看某个万得接口（wind_list_apis 返回的 name）的完整参数与返回说明。",
        input_schema={
            "type": "object",
            "properties": {"api_name": {"type": "string", "description": "接口名，如 wind_get_stock_quote"}},
            "required": ["api_name"],
        },
    ),
    ToolSpec(
        name=WIND_QUERY,
        description="按 wind_get_api_info 确认好的参数调用万得接口取数。",
        input_schema={
            "type": "object",
            "properties": {
                "api_name": {"type": "string", "description": "接口名，如 wind_get_stock_quote"},
                "params": {"type": "object", "description": "该接口的参数对象，形状以 wind_get_api_info 为准"},
            },
            "required": ["api_name"],
        },
    ),
)

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

# 掉线域的重试冷却（秒）：避免持续故障时每次 wind_* 元工具调用都白等一次连接，
# 同时保证一次瞬时故障（如 502 Bad Gateway）不会把该域永久打入冷宫直到 30 分钟后的
# 进程级工具池整体重建（见 _ensure_concrete_catalog）。
_DOMAIN_RETRY_COOLDOWN = 60.0


class WindToolProvider:
    """把万得 7 个域合并成一个带 `wind_` 前缀的 ToolProvider；懒连接、单域失败隔离、永不抛。

    对外（`list_tools()`）只暴露 3 个懒发现元工具，35 个具体接口仅在内部目录缓存、经元工具按需查询/分发。
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        max_concurrency: int = 5,
        keepalive_interval: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._retries = retries
        self._keepalive_interval = keepalive_interval
        self._max_concurrency = max_concurrency
        self._semaphore: asyncio.Semaphore | None = None
        self._stack: AsyncExitStack | None = None
        self._providers: dict[str, MCPToolProvider] = {}
        self._index: dict[str, str] = {}  # 原始工具名 → 域（按域累积，不整体重建）
        self._specs: list[ToolSpec] = []  # 具体 35 个 wind_* ToolSpec（内部缓存，不对外暴露；按域累积）
        self._by_name: dict[str, ToolSpec] = {}  # 带前缀全名 → ToolSpec，供 wind_get_api_info/wind_list_apis 查询
        self._ok_domains: set[str] = set()  # 已成功编目的域（跳过重复连接开销）
        self._domain_retry_after: dict[str, float] = {}  # 失败域 → 下次可重试的 monotonic 时间戳
        # 2026-09-07 审查：热启动 warm_up 与首个 /chat 的 list_tools 并发调用过 _ensure_concrete_catalog，
        # check-then-await 无锁 → 同一批域被连接/编目两次，_specs 重复出现（wind_list_apis 计数翻倍）。
        # _catalog_lock 单飞目录构建；_provider_locks 防 _dispatch 与编目并发双开同域会话——按域一把锁
        # （而非全局一把），使 _ensure_concrete_catalog 能对不同域真正并发连接，不在锁上排队退化成串行。
        self._catalog_lock = asyncio.Lock()
        self._provider_locks: dict[str, asyncio.Lock] = {}

    async def __aenter__(self) -> Self:
        self._stack = AsyncExitStack()
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    @asynccontextmanager
    async def _open(self, domain: str) -> AsyncIterator[MCPToolProvider]:
        """唯一的连接/鉴权点：每域一条 streamable-http 会话，复用 MCPToolProvider。

        仅当配置了保活间隔才传 keepalive_interval（缺失参数时保持旧签名行为，兼容测试的 fake CM）。
        """
        endpoint = WIND_DOMAINS[domain]
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        kwargs: dict[str, object] = {"timeout": self._timeout, "retries": self._retries, "headers": headers}
        if self._keepalive_interval:
            kwargs["keepalive_interval"] = self._keepalive_interval
        async with mcp_tool_provider(endpoint, **kwargs) as provider:
            yield provider

    async def _get_provider(self, domain: str) -> MCPToolProvider:
        if domain not in self._providers:
            # setdefault 在两次 await 之间不让出，同一域的锁对象在并发协程间天然只会创建一次。
            lock = self._provider_locks.setdefault(domain, asyncio.Lock())
            async with lock:
                if domain not in self._providers:
                    assert self._stack is not None
                    self._providers[domain] = await self._stack.enter_async_context(self._open(domain))
        return self._providers[domain]

    async def _connect_domain(self, domain: str) -> list[ToolSpec] | None:
        """连单个域并拉工具清单；失败记冷却、返回 None（调用方决定如何合并，隔离语义不变）。"""
        try:
            provider = await self._get_provider(domain)
            return await provider.list_tools()
        except Exception as exc:  # noqa: BLE001 - 单域不可用不阻断，其余域照常暴露，冷却后重试
            print(f"[wind] 域 {domain} 不可用，{_DOMAIN_RETRY_COOLDOWN:.0f}s 后重试：{exc}")
            self._domain_retry_after[domain] = time.monotonic() + _DOMAIN_RETRY_COOLDOWN
            return None

    async def _ensure_concrete_catalog(self) -> None:
        """连未编目成功的域、拉具体 35 个 wind_* ToolSpec，累积进 self._specs/_index/_by_name。

        按域跟踪成功状态（_ok_domains）：已成功的域跳过（不重复连接开销）；失败的域在
        `_DOMAIN_RETRY_COOLDOWN` 冷却期后才重试——2026-09 事故：此前是「只做一次」的永久缓存，
        某域在建目录那一刻若恰好瞬时故障（如 502 Bad Gateway）就会被永久打入冷宫，直到 30 分钟后
        进程级工具池整体重建（`entry/web.py` 的 `_recycle_tools_loop`）才会恢复；冷却窗口让它能在
        一分钟量级内自愈，同时不会在持续故障时让每次 wind_* 元工具调用都白等一次连接。

        各待连域**并发**连接（`asyncio.gather`）而非逐个 `await`——7 个域串行时总耗时是「域数 ×
        单域握手耗时」的线性叠加（网关/demomcp 开关万得后首次编目最慢的一环）；并发后总耗时约等于
        「最慢的一域」。合并结果时按 `pending`（即 `WIND_DOMAINS` 声明顺序）而非完成顺序回填
        `self._specs`，让 `wind_list_apis` 等返回顺序在多次运行间保持确定性，不随并发调度抖动。
        """
        now = time.monotonic()
        async with self._catalog_lock:
            pending = [
                d for d in WIND_DOMAINS
                if d not in self._ok_domains and self._domain_retry_after.get(d, 0.0) <= now
            ]
            if not pending:
                return
            results = await asyncio.gather(*(self._connect_domain(d) for d in pending))
            changed = False
            for domain, domain_specs in zip(pending, results, strict=True):
                if domain_specs is None:
                    continue
                self._ok_domains.add(domain)
                self._domain_retry_after.pop(domain, None)
                changed = True
                for spec in domain_specs:
                    prefixed = replace(spec, name=f"{PREFIX}{spec.name}")
                    self._specs.append(prefixed)
                    self._index[spec.name] = domain
            if changed:
                self._by_name = {s.name: s for s in self._specs}
                _log_registered_wind(self._specs)

    async def warm_up(self) -> None:
        """热启动：预连 7 个域并拉具体目录（单域失败隔离，永远不抛）。"""
        await self._ensure_concrete_catalog()

    async def list_tools(self) -> list[ToolSpec]:
        """对外只暴露懒发现三件套（wind_list_apis/wind_get_api_info/wind_query），不摊开 35 个具体 schema。

        仍会预热具体目录（_ensure_concrete_catalog），这样首次 wind_list_apis/wind_get_api_info
        调用无需再等一轮域连接。
        """
        await self._ensure_concrete_catalog()
        return list(_WIND_META_SPECS)

    def _target_name(self, args: dict[str, Any]) -> str | None:
        """从 {api_name} 里取目标工具的带前缀全名；接受裸名（不带 wind_ 前缀）。"""
        raw = args.get("api_name")
        if not raw:
            return None
        raw = str(raw)
        return raw if raw.startswith(PREFIX) else f"{PREFIX}{raw}"

    async def _call_list_apis(self, args: dict[str, Any]) -> ToolResult:
        await self._ensure_concrete_catalog()
        keyword = str(args.get("keyword") or "").strip().lower()
        items = [
            {"name": s.name, "description": s.description}
            for s in (self._specs or [])
            if not keyword or keyword in s.name.lower() or keyword in s.description.lower()
        ]
        return ToolResult(content=json.dumps({"count": len(items), "apis": items}, ensure_ascii=False))

    async def _call_get_api_info(self, args: dict[str, Any]) -> ToolResult:
        await self._ensure_concrete_catalog()
        target = self._target_name(args)
        spec = self._by_name.get(target or "")
        if spec is None:
            return ToolResult(content=f"未知万得接口：{args.get('api_name')}", is_error=True)
        payload = {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))

    async def _call_query(self, args: dict[str, Any]) -> ToolResult:
        await self._ensure_concrete_catalog()
        target = self._target_name(args)
        if target is None:
            return ToolResult(content="wind_query 缺少 api_name 参数", is_error=True)
        params = args.get("params")
        if params is not None and not isinstance(params, dict):
            return ToolResult(content="wind_query 的 params 必须是对象", is_error=True)
        return await self._dispatch(target, params or {})

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = arguments or {}
        if name == WIND_LIST_APIS:
            return await self._call_list_apis(args)
        if name == WIND_GET_API_INFO:
            return await self._call_get_api_info(args)
        if name == WIND_QUERY:
            return await self._call_query(args)
        return await self._dispatch(name, args)

    async def _dispatch(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """按带前缀全名直连具体万得接口：剥前缀、按域路由、限流、错误信封归一化（原 call_tool 逻辑不变）。

        wind_query 与（历史遗留的）直接按具体名调用都走这里，保证两条路径行为一致。
        """
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
    """打印一次已自动发现的万得内部具体工具清单（只打印，不改返回）；对 LLM 实际只暴露 3 个懒发现元工具。"""
    names = [s.name for s in specs]
    shown = ", ".join(names[:100]) + (" …" if len(names) > 100 else "")
    print(f"[wind] 已连接，内部目录 {len(names)} 个（{PREFIX} 前缀，经 3 个元工具懒发现）：{shown}")


# 注：原先这里还有一个 `agent_tool_provider(settings)`，把 Tushare + Wind 在 **demomcp 进程内**
# 组装成 CompositeToolProvider。它已随「MCP 网关成为唯一取数路径」删除——多源组装与按源开关现在是
# `mcp_gateway/`（独立进程/端口）的职责，见 mcp_gateway/sources.py（复用下面的 WindToolProvider）
# 与 mcp_gateway/pool.py（GatewayToolProvider 负责跨源聚合）。demomcp 只经
# `entry/{web,cli}.py` 连网关一个地址，进程内不再直连任何数据源。
