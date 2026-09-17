"""每源一个连接池的生命周期 + 跨源聚合成一个 ToolProvider。

`SourcePool` 把 demomcp/entry/web.py 里已经验证过的 `_ToolPool`/`_acquire_tools`/`_retire_pool`/
`_recycle_tools_loop`/`_hot_start_tools` 那一整套逻辑搬过来，参数化成「按 source_id 一份」而不是
进程级一份：懒建 + 双检锁、热启动指数退避重试、周期重建（自愈）、开关切换时摘池 + 后台等在途请求结束
再真正关闭。`GatewayToolProvider` 按当前**已启用**的源聚合 `list_tools`/`call_tool`（对外满足
demomcp `interfaces.tool_provider.ToolProvider` 协议的形状，供 mcp_endpoint.py 包成 MCP server），
同时提供 `status()`/`set_enabled()` 给 admin.py 用。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.interfaces.types import ToolResult, ToolSpec
from demomcp.providers.tools.mcp import describe_exception
from mcp_gateway.sources import SourceDef
from mcp_gateway.toggle_store import load_toggle_state, save_source_enabled

logger = logging.getLogger("mcp_gateway.pool")

RECYCLE_INTERVAL = 1800.0
HOT_START_BACKOFF_BASE = 5.0
HOT_START_BACKOFF_MAX = 60.0


@dataclass
class _Pool:
    provider: ToolProvider
    stack: AsyncExitStack
    users: int = 0
    retired: bool = False


class SourcePool:
    """一个上游源的连接生命周期 + 开关状态（懒建、热启动重试、周期重建、开关切换即摘池/重连）。"""

    def __init__(self, source: SourceDef, *, enabled: bool, data_dir: str) -> None:
        self.source = source
        self.enabled = enabled
        self._data_dir = data_dir
        self._pool: _Pool | None = None
        self._retired: list[_Pool] = []
        self._lock = asyncio.Lock()
        # 最近一次编目到的工具数（由 GatewayToolProvider.list_tools 顺手记下——它本来就要遍历每个源，
        # 所以这里是零成本；不能只读 provider._specs：那是 Wind/Composite 的内部字段，
        # 裸 MCPToolProvider（如 tushare 源）没有它，会永远显示「未知」）。
        self.last_tool_count: int | None = None

    @property
    def connected(self) -> bool:
        return self._pool is not None

    async def tool_count(self) -> int | None:
        """已编目就直接给数；连着但还没编目过（热启动只做了 warm_up）→ 现拉一次并缓存。

        只在 /admin/sources 这类人看的状态查询里被调到，一个源一次往返完全可接受；失败返回 None
        （显示成「未知」）而不是抛错。
        """
        if self._pool is None:
            return None
        if self.last_tool_count is None:
            provider = None
            try:
                provider = await self.acquire()
                self.last_tool_count = len(await provider.list_tools())
            except Exception as exc:  # noqa: BLE001 - 状态查询尽力而为，拿不到就显示未知
                logger.debug("[%s] 取工具数失败：%s", self.source.id, describe_exception(exc))
            finally:
                if provider is not None:
                    await self.release(provider)
        return self.last_tool_count

    async def acquire(self) -> ToolProvider:
        if not self.enabled:
            raise RuntimeError(f"数据源 {self.source.id} 已停用")
        pool = self._pool
        if pool is None:
            async with self._lock:
                pool = self._pool
                if pool is None:
                    stack = AsyncExitStack()
                    pool = _Pool(provider=await stack.enter_async_context(self.source.connect()), stack=stack)
                    self._pool = pool
        pool.users += 1
        return pool.provider

    async def release(self, provider: ToolProvider) -> None:
        pool = self._pool
        if pool is not None and pool.provider is provider:
            pool.users = max(0, pool.users - 1)
            return
        for retired in self._retired:
            if retired.provider is provider:
                retired.users = max(0, retired.users - 1)
                return

    async def _retire(self, pool: _Pool) -> None:
        logger.info("[%s] 连接池已退休；等待 %d 个在途请求结束", self.source.id, pool.users)
        try:
            while pool.users > 0:
                await asyncio.sleep(0.5)
        finally:
            with contextlib.suppress(Exception):
                await pool.stack.aclose()
            with contextlib.suppress(ValueError):
                self._retired.remove(pool)

    async def set_enabled(self, enabled: bool, *, persist: bool = True) -> None:
        if persist:
            save_source_enabled(self._data_dir, self.source.id, enabled)
        self.enabled = enabled
        if not enabled:
            self.last_tool_count = None  # 别把停用前的旧计数留在状态里
            async with self._lock:
                old = self._pool
                self._pool = None
                if old is not None:
                    old.retired = True
                    self._retired.append(old)
            if old is not None:
                asyncio.create_task(self._retire(old))
        else:
            asyncio.create_task(self._reconnect_once())

    async def _reconnect_once(self) -> None:
        provider = None
        try:
            provider = await self.acquire()
            warm = getattr(provider, "warm_up", None)
            if warm is not None:
                await warm()
        except Exception as exc:  # noqa: BLE001 - 尽力而为，失败留给下次调用懒建
            logger.warning("[%s] 重连尝试失败：%s", self.source.id, describe_exception(exc))
        finally:
            if provider is not None:
                await self.release(provider)

    async def hot_start(self) -> None:
        """热启动：不断退避重试直到成功；enabled=False 时不启动，等用户手动打开再连。"""
        if not self.enabled:
            return
        delay = 0.0
        while True:
            provider = None
            try:
                provider = await self.acquire()
                warm = getattr(provider, "warm_up", None)
                if warm is not None:
                    await warm()
                logger.info("[%s] 热启动完成", self.source.id)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 热启动非致命：失败记日志并退避重试
                delay = HOT_START_BACKOFF_BASE if delay == 0.0 else min(delay * 2, HOT_START_BACKOFF_MAX)
                logger.warning(
                    "[%s] 热启动失败，%.0fs 后重试：%s", self.source.id, delay, describe_exception(exc)
                )
                await asyncio.sleep(delay)
            finally:
                if provider is not None:
                    await self.release(provider)

    async def recycle(self) -> None:
        """周期重建（自愈）：开关关闭期间跳过，不偷偷重连。"""
        if not self.enabled:
            return
        stack = AsyncExitStack()
        try:
            new_provider = await stack.enter_async_context(self.source.connect())
        except Exception as exc:  # noqa: BLE001 - 重建失败沿用旧池，下一轮再试
            with contextlib.suppress(Exception):
                await stack.aclose()
            logger.warning("[%s] 周期重建失败，沿用旧池：%s", self.source.id, describe_exception(exc))
            return
        async with self._lock:
            old = self._pool
            self._pool = _Pool(new_provider, stack)
            if old is not None:
                old.retired = True
                self._retired.append(old)
        if old is not None:
            await self._retire(old)

    async def close(self) -> None:
        async with self._lock:
            old = self._pool
            self._pool = None
        if old is not None:
            with contextlib.suppress(Exception):
                await old.stack.aclose()
        for retired in list(self._retired):
            with contextlib.suppress(Exception):
                await retired.stack.aclose()
        self._retired.clear()


class GatewayToolProvider:
    """跨源聚合：`list_tools`/`call_tool` 满足 `ToolProvider` 协议，只看当前已启用的源。

    调用名 -> 源 id 的映射在每次 `list_tools()` 时刷新并缓存（同 `WindToolProvider`/
    `CompositeToolProvider` 已有的「编目时建索引、调用时查索引」手法）；`call_tool` 用该缓存路由，
    缓存为空/未命中该名字时明确报错，不猜测。
    """

    def __init__(self, sources: list[SourceDef], *, data_dir: str) -> None:
        toggle_state = load_toggle_state(data_dir)
        self.pools: dict[str, SourcePool] = {
            s.id: SourcePool(s, enabled=toggle_state.get(s.id, s.default_enabled), data_dir=data_dir)
            for s in sources
        }
        self._name_to_source: dict[str, str] = {}
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(p.hot_start()) for p in self.pools.values()]
        self._tasks.append(asyncio.create_task(self._recycle_forever()))

    async def _recycle_forever(self) -> None:
        while True:
            await asyncio.sleep(RECYCLE_INTERVAL)
            for pool in self.pools.values():
                await pool.recycle()

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        self._tasks.clear()
        for pool in self.pools.values():
            await pool.close()

    async def status(self) -> list[dict]:
        out = []
        for pool in self.pools.values():
            out.append(
                {
                    "id": pool.source.id,
                    "display_name": pool.source.display_name,
                    "enabled": pool.enabled,
                    "connected": pool.connected,
                    "tool_count": await pool.tool_count(),
                }
            )
        return out

    async def set_enabled(self, source_id: str, enabled: bool) -> bool:
        """切换某源开关；source_id 不存在返回 False（调用方转 404），否则 True。"""
        pool = self.pools.get(source_id)
        if pool is None:
            return False
        await pool.set_enabled(enabled)
        return True

    # ---- ToolProvider 协议：只看已启用的源 ----

    async def _list_source_tools(self, source_id: str, pool: SourcePool) -> list[ToolSpec]:
        """单源 acquire+list_tools+release；失败原样吞成空列表（隔离语义不变，供 list_tools 并发调用）。"""
        try:
            provider = await pool.acquire()
        except Exception as exc:  # noqa: BLE001 - 单源不可用不拖垮整体清单
            logger.warning("[%s] 获取连接失败，本轮跳过：%s", source_id, describe_exception(exc))
            return []
        try:
            pool_specs = await provider.list_tools()
        except Exception as exc:  # noqa: BLE001 - 单源 list_tools 失败不拖垮整体清单
            logger.warning("[%s] list_tools 失败，本轮跳过：%s", source_id, describe_exception(exc))
            pool_specs = []
        finally:
            await pool.release(provider)
        pool.last_tool_count = len(pool_specs)  # 供 /admin/sources 显示（顺手记，不额外发请求）
        return pool_specs

    async def list_tools(self) -> list[ToolSpec]:
        """跨源聚合工具清单：各已启用源**并发**拉取（各源有独立 `SourcePool._lock`，互不阻塞），
        而非逐源 `await`——避免某源恰好冷连接（如开关刚打开）时拖慢其它已就绪源的清单获取。
        结果按 `self.pools` 声明顺序（而非完成顺序）合并，保持返回顺序确定性。
        """
        enabled = [(source_id, pool) for source_id, pool in self.pools.items() if pool.enabled]
        results = await asyncio.gather(*(self._list_source_tools(sid, pool) for sid, pool in enabled))
        specs: list[ToolSpec] = []
        name_to_source: dict[str, str] = {}
        for (source_id, _pool), pool_specs in zip(enabled, results, strict=True):
            for spec in pool_specs:
                # 跨源撞名：**先声明的源赢**，重复的那个整条丢掉并告警。
                # 两件事都必须做：(1) 同名工具只能有一个归属，否则 call_tool 的路由会随
                # list_tools 的写入顺序漂；(2) specs 里留两条同名 ToolSpec 会让下游把重复的
                # tool 定义塞进 llm.chat(tools=…)，那是非法载荷。源多了以后这是真会踩的坑
                # （各源前缀本来互不重叠：wind_/ifind_ 有前缀，Tushare 与免费源用各自的原生名），
                # 一旦告警出现就说明某个源换了命名，去 sources.py 给它加前缀，别指望这里兜住。
                if spec.name in name_to_source:
                    logger.warning(
                        "工具名冲突：%s 同时由 %s 与 %s 暴露；保留先声明的 %s，忽略 %s 的那一条。",
                        spec.name, name_to_source[spec.name], source_id,
                        name_to_source[spec.name], source_id,
                    )
                    continue
                specs.append(spec)
                name_to_source[spec.name] = source_id
        self._name_to_source = name_to_source
        return specs

    async def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        source_id = self._name_to_source.get(name)
        if source_id is None or source_id not in self.pools:
            return ToolResult(content=f"Unknown tool: {name}", is_error=True)
        pool = self.pools[source_id]
        if not pool.enabled:
            return ToolResult(content=f"数据源 {source_id} 已停用，无法调用 {name}。", is_error=True)
        try:
            provider = await pool.acquire()
        except Exception as exc:  # noqa: BLE001 - 连接失败转 is_error，不崩
            return ToolResult(content=f"数据源 {source_id} 连接失败：{exc}", is_error=True)
        try:
            return await provider.call_tool(name, arguments)
        finally:
            await pool.release(provider)
