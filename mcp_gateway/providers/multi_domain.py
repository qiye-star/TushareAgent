"""多域远程 MCP 源的通用外壳：把「一个数据商 = N 个独立 MCP 端点」收敛成一个 ToolProvider。

万得（Wind）与同花顺（iFind）在形态上是同一个东西：数据商按业务域（股票/基金/债券/宏观/新闻/…）
各开一个 streamable-http MCP 端点，用同一个 key 鉴权。本模块把这套形态里与数据商无关的部分
参数化出来，供 `ifind.py` 之类的具体源直接复用：

- **懒连接 + 按域隔离**：某域连不上不影响其它域（`_connect_domain` 返回 None 而非抛）；
- **失败域冷却重试**（`DOMAIN_RETRY_COOLDOWN`）：一次瞬时 502 不会把该域打入冷宫到进程重启；
- **目录构建单飞**（`_catalog_lock`）：热启动的 warm_up 与首个请求的 list_tools 并发时不会重复编目；
- **按域一把连接锁**（`_provider_locks`）：不同域仍能真正并发连接，不在一把全局锁上退化成串行；
- **懒发现三件套**：对外只暴露 `<prefix>list_apis` / `<prefix>get_api_info` / `<prefix>query`
  三个元工具，具体接口 schema 只进内部目录、经元工具按需查询——避免把几十个 schema 塞进
  每轮 `llm.chat(tools=…)` 的载荷（与 Tushare 官方 MCP 的 list_apis/get_api_info/query 手法一致）。

行为参照 `demomcp/providers/tools/wind.py`（已在线上验证的那份）；wind.py 自身有测试锁定其
实现细节，故本模块**不去改它**，只把同一套模式泛化给新源用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from typing import Any, Self

from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.mcp import MCPToolProvider, mcp_tool_provider

logger = logging.getLogger("mcp_gateway.providers")

# 掉线域的重试冷却（秒）：持续故障时不让每次元工具调用都白等一次连接，
# 同时保证瞬时故障能在一分钟量级自愈（而不是等 pool.py 的 30 分钟整池重建）。
DOMAIN_RETRY_COOLDOWN = 60.0


def build_meta_specs(prefix: str, label: str, *, api_name_example: str) -> tuple[ToolSpec, ...]:
    """生成某个源的懒发现三件套 ToolSpec（名字带 prefix，描述里带中文源名）。"""
    return (
        ToolSpec(
            name=f"{prefix}list_apis",
            description=(
                f"浏览{label}可用接口名与简介，可按关键词过滤（如 行情/财务/公告/新闻/宏观/基金/债券/指数）。"
                f"拿不准该用哪个{label}接口时先调用这个。"
            ),
            input_schema={
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "可选关键词，按名字/简介子串过滤"}},
            },
        ),
        ToolSpec(
            name=f"{prefix}get_api_info",
            description=f"查看某个{label}接口（{prefix}list_apis 返回的 name）的完整参数与返回说明。",
            input_schema={
                "type": "object",
                "properties": {
                    "api_name": {"type": "string", "description": f"接口名，如 {api_name_example}"}
                },
                "required": ["api_name"],
            },
        ),
        ToolSpec(
            name=f"{prefix}query",
            description=f"按 {prefix}get_api_info 确认好的参数调用{label}接口取数。",
            input_schema={
                "type": "object",
                "properties": {
                    "api_name": {"type": "string", "description": f"接口名，如 {api_name_example}"},
                    "params": {
                        "type": "object",
                        "description": f"该接口的参数对象，形状以 {prefix}get_api_info 为准",
                    },
                },
                "required": ["api_name"],
            },
        ),
    )


class MultiDomainMCPProvider:
    """N 个远程 MCP 端点合成一个带前缀的 ToolProvider；懒连接、单域失败隔离、永不抛。

    子类/调用方需提供：`domains`（域名→URL）、`prefix`、`label`（中文源名，进日志与元工具描述）、
    `headers`（鉴权头，None 表示不加）、可选 `error_signal`（把该数据商的错误信封翻成一句人话）。
    """

    def __init__(
        self,
        domains: Mapping[str, str],
        *,
        prefix: str,
        label: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        retries: int = 2,
        max_concurrency: int = 5,
        keepalive_interval: float | None = None,
        error_signal: Callable[[str], str | None] | None = None,
        business_error: Callable[[str], bool] | None = None,
        api_name_example: str = "",
    ) -> None:
        self._domains = dict(domains)
        self._prefix = prefix
        self._label = label
        self._headers = headers
        self._timeout = timeout
        self._retries = retries
        self._max_concurrency = max_concurrency
        self._keepalive_interval = keepalive_interval
        self._error_signal = error_signal
        self._business_error = business_error
        self._meta_specs = build_meta_specs(
            prefix, label, api_name_example=api_name_example or f"{prefix}get_stock_info"
        )
        self._semaphore: asyncio.Semaphore | None = None
        self._stack: AsyncExitStack | None = None
        self._providers: dict[str, MCPToolProvider] = {}
        self._index: dict[str, str] = {}  # 原始（无前缀）工具名 → 域
        self._specs: list[ToolSpec] = []  # 具体接口 ToolSpec（带前缀，内部目录，不对外暴露）
        self._by_name: dict[str, ToolSpec] = {}
        self._ok_domains: set[str] = set()
        self._domain_retry_after: dict[str, float] = {}
        self._catalog_lock = asyncio.Lock()
        self._provider_locks: dict[str, asyncio.Lock] = {}

    # ---- 生命周期 ----

    async def __aenter__(self) -> Self:
        self._stack = AsyncExitStack()
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    @asynccontextmanager
    async def _open(self, domain: str) -> AsyncIterator[MCPToolProvider]:
        """唯一的连接/鉴权点：每域一条 streamable-http 会话，复用 MCPToolProvider 的保活/重连。"""
        kwargs: dict[str, object] = {
            "timeout": self._timeout,
            "retries": self._retries,
            "headers": self._headers,
        }
        if self._keepalive_interval:
            kwargs["keepalive_interval"] = self._keepalive_interval
        # 只在源真的覆盖了判据时才传：缺省不传，保持 mcp_tool_provider 的旧签名调用形状
        # （测试里注入的假 CM 未必接受这个 kwarg）。
        if self._business_error is not None:
            kwargs["business_error"] = self._business_error
        async with mcp_tool_provider(self._domains[domain], **kwargs) as provider:
            yield provider

    async def _get_provider(self, domain: str) -> MCPToolProvider:
        if domain not in self._providers:
            # setdefault 在两次 await 之间不让出，同一域的锁对象在并发协程间只会创建一次。
            lock = self._provider_locks.setdefault(domain, asyncio.Lock())
            async with lock:
                if domain not in self._providers:
                    if self._stack is None:
                        raise RuntimeError(f"{self._label} provider 未进入上下文（缺 async with）")
                    self._providers[domain] = await self._stack.enter_async_context(self._open(domain))
        return self._providers[domain]

    # ---- 目录构建 ----

    async def _connect_domain(self, domain: str) -> list[ToolSpec] | None:
        """连单个域并拉工具清单；失败记冷却、返回 None（隔离语义：不影响其它域）。"""
        try:
            provider = await self._get_provider(domain)
            return await provider.list_tools()
        except Exception as exc:  # noqa: BLE001 - 单域不可用不阻断，冷却后重试
            logger.warning(
                "[%s] 域 %s 不可用，%.0fs 后重试：%s", self._prefix.rstrip("_"), domain,
                DOMAIN_RETRY_COOLDOWN, exc,
            )
            self._domain_retry_after[domain] = time.monotonic() + DOMAIN_RETRY_COOLDOWN
            return None

    async def _ensure_catalog(self) -> None:
        """连未编目成功的域、拉具体接口 ToolSpec 累积进内部目录。

        已成功的域跳过；失败的域过冷却期才重试。待连域**并发**连接，合并时按声明顺序回填，
        使 list_apis 的返回顺序在多次运行间保持确定、不随并发调度抖动。
        """
        now = time.monotonic()
        async with self._catalog_lock:
            pending = [
                d
                for d in self._domains
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
                    self._specs.append(replace(spec, name=f"{self._prefix}{spec.name}"))
                    self._index[spec.name] = domain
            if changed:
                self._by_name = {s.name: s for s in self._specs}
                names = [s.name for s in self._specs]
                shown = ", ".join(names[:100]) + (" …" if len(names) > 100 else "")
                print(
                    f"[{self._prefix.rstrip('_')}] 已连接，内部目录 {len(names)} 个"
                    f"（{self._prefix} 前缀，经 3 个元工具懒发现）：{shown}"
                )

    async def warm_up(self) -> None:
        """热启动：预连各域并拉具体目录（单域失败隔离，永远不抛）。"""
        await self._ensure_catalog()

    async def list_tools(self) -> list[ToolSpec]:
        """对外只暴露懒发现三件套；仍预热具体目录，让首次 list_apis 无需再等一轮域连接。"""
        await self._ensure_catalog()
        return list(self._meta_specs)

    # ---- 元工具 ----

    def _target_name(self, args: dict[str, Any]) -> str | None:
        """从 {api_name} 取目标工具的带前缀全名；接受裸名（不带前缀）。"""
        raw = args.get("api_name")
        if not raw:
            return None
        raw = str(raw)
        return raw if raw.startswith(self._prefix) else f"{self._prefix}{raw}"

    async def _call_list_apis(self, args: dict[str, Any]) -> ToolResult:
        await self._ensure_catalog()
        keyword = str(args.get("keyword") or "").strip().lower()
        items = [
            {"name": s.name, "description": s.description}
            for s in self._specs
            if not keyword or keyword in s.name.lower() or keyword in s.description.lower()
        ]
        return ToolResult(content=json.dumps({"count": len(items), "apis": items}, ensure_ascii=False))

    async def _call_get_api_info(self, args: dict[str, Any]) -> ToolResult:
        await self._ensure_catalog()
        spec = self._by_name.get(self._target_name(args) or "")
        if spec is None:
            return ToolResult(content=f"未知{self._label}接口：{args.get('api_name')}", is_error=True)
        payload = {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))

    async def _call_query(self, args: dict[str, Any]) -> ToolResult:
        await self._ensure_catalog()
        target = self._target_name(args)
        if target is None:
            return ToolResult(content=f"{self._prefix}query 缺少 api_name 参数", is_error=True)
        params = args.get("params")
        if params is not None and not isinstance(params, dict):
            return ToolResult(content=f"{self._prefix}query 的 params 必须是对象", is_error=True)
        return await self._dispatch(target, params or {})

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        args = arguments or {}
        if name == f"{self._prefix}list_apis":
            return await self._call_list_apis(args)
        if name == f"{self._prefix}get_api_info":
            return await self._call_get_api_info(args)
        if name == f"{self._prefix}query":
            return await self._call_query(args)
        # 历史遗留/直接按具体名调用也走同一条分发路径，保证两条路径行为一致。
        return await self._dispatch(name, args)

    async def _dispatch(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """按带前缀全名直连具体接口：剥前缀、按域路由、限流、错误信封归一化。"""
        raw = name[len(self._prefix) :] if name.startswith(self._prefix) else None
        domain = self._index.get(raw or "")
        if raw is None or domain is None:
            return ToolResult(content=f"Unknown {self._prefix.rstrip('_')} tool: {name}", is_error=True)
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrency)
        try:
            async with self._semaphore:
                try:
                    provider = await self._get_provider(domain)
                except Exception as exc:  # noqa: BLE001 - 连接失败转 is_error，不崩图
                    return ToolResult(content=f"[{self._label}] 连接域 {domain} 失败: {exc}", is_error=True)
                result = await provider.call_tool(raw, arguments)
        except Exception as exc:  # noqa: BLE001 - 调用异常转 is_error
            return ToolResult(content=f"[{self._label}] 调用 {raw} 失败: {exc}", is_error=True)
        if result.is_error:
            return result
        if self._error_signal is not None:
            signal = self._error_signal(result.content)
            if signal:
                return ToolResult(content=signal, is_error=True)
        return result
