"""MCP 工具来源：作为 MCP 客户端经 HTTP（streamable-http）连接独立 mcp_server，把 FastMCP 工具直译为中立 ToolSpec。

整个会话复用一条 MCP 连接。工具返回 {code,msg,row_count,data} 是 HTTP 200 的正常字典，
code!=0 视为业务失败；调用出错（异常或业务失败）会重试几次，若最终是权限类失败则在结果里明确
提示「积分不足/需更高积分」，由 LLM 如实转述给用户（而非假装取到数）。

连接生命周期（owned 模式，见 MCPToolProvider）：持有 url 时由 provider 自己管理连接——
进 `mcp_tool_provider` 即连（供 web 热启动）、`send_ping` 保活防断联（远端/NAT 掐线自动感知）、
传输层断线自动重建会话（重新启动机制）；legacy 注入 session 的路径（测试用）不受影响。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from datetime import timedelta
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.exceptions import McpError
from mcp.types import Tool as MCPTool

from demomcp.interfaces.types import ToolResult, ToolSpec

logger = logging.getLogger("demomcp.mcp")

# _invalidate 的「不检查身份」哨兵：传对象时只拆「正是该会话」的当前连接（保活迟到失败不误杀重建后的新会话）
_NO_EXPECT = object()


def to_tool_spec(tool: MCPTool) -> ToolSpec:
    """把 mcp.types.Tool 直译为中立 ToolSpec（name / description / inputSchema）。"""
    return ToolSpec(name=tool.name, description=tool.description or "", input_schema=tool.inputSchema)


def _is_transport_failure(exc: Exception) -> bool:
    """是否传输/断线类失败：连接层（httpx/OSError/超时）或会话被远端关闭（-32000）/应答超时（408）。

    也识别 anyio.ClosedResourceError/BrokenResourceError（2026-09-07 事故）：`_keepalive_loop`
    的 `_invalidate()` 不持锁，可能与另一协程正在进行的 `session.list_tools()`/`call_tool()` 并发，
    后者会在已关闭的底层流上读出这两种异常——它们本质就是传输层失败，此前漏判导致既不重建也不友好降级。

    显式排除协议级错误（如 -32601 Method not found：服务器活着但没实现某方法）——重建无益，防重启风暴。
    """
    if isinstance(exc, (httpx.HTTPError, OSError, TimeoutError, anyio.ClosedResourceError, anyio.BrokenResourceError)):
        return True
    if isinstance(exc, McpError):
        code = getattr(getattr(exc, "error", None), "code", None)
        return code in (-32000, 408)
    return False


def describe_exception(exc: BaseException, *, max_leaves: int = 3) -> str:
    """把异常（含 anyio TaskGroup 产生的 ExceptionGroup）展开成人能看懂的一行。

    `BaseExceptionGroup.__str__` 固定返回「unhandled errors in a TaskGroup (N sub-exceptions)」，
    真正的失败原因（连接被拒/协议不对/超时…）全部丢在 `.exceptions` 里、日志里一个字都看不到——
    `streamablehttp_client` 内部用 anyio TaskGroup，建连失败时抛的就是这种组合异常。这里递归拆到
    叶子异常，取前 max_leaves 个拼成一行，其余记数省略，供各处 `logger.warning(..., exc)` 调用替换
    裸 `%s` 格式化。
    """
    leaves: list[BaseException] = []

    def _collect(e: BaseException) -> None:
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                _collect(sub)
        else:
            leaves.append(e)

    _collect(exc)
    if not leaves:
        return f"{type(exc).__name__}: {exc}"
    shown = [f"{type(e).__name__}: {e}" for e in leaves[:max_leaves]]
    if len(leaves) > max_leaves:
        shown.append(f"…+{len(leaves) - max_leaves} more")
    return "; ".join(shown)


@asynccontextmanager
async def _streamable_session_factory(
    url: str, *, headers: dict[str, str] | None = None, timeout: float = 30.0
) -> AsyncIterator[Any]:
    """真正的建连工厂：streamable-http + ClientSession + initialize（每调一次=一条新会话）。

    给 ClientSession 传 read_timeout_seconds：list_tools/send_ping 也有应答时限，不会把保活循环拖死；
    per-call 的 call_tool(read_timeout_seconds=...) 优先级更高（SDK 已核实）。
    """
    async with streamablehttp_client(
        url, headers=headers, timeout=timeout, sse_read_timeout=timeout
    ) as (read, write, _session_id), ClientSession(
        read, write, read_timeout_seconds=timedelta(seconds=timeout)
    ) as session:
        await session.initialize()
        yield session


class MCPToolProvider:
    def __init__(
        self,
        session: Any = None,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        keepalive_interval: float | None = None,
        reconnect: bool = True,
        session_factory: Any = None,
        business_error: Callable[[str], bool] | None = None,
    ) -> None:
        """两种模式：

        - legacy（直接注入 session，如 test_tool_provider）：不持有 url、不建栈、不保活、不重连——
          行为与旧版逐字节一致；
        - owned（url 或 session_factory 给出，session 为空）：持有连接生命周期——断线重建 + 保活 ping。
        session_factory 是测试注入点（async generator，返回值契约同 _streamable_session_factory）。

        `business_error` 覆盖「怎样算业务失败」的判据，默认 `_is_business_error`（Tushare 约定：
        JSON 里 `code != 0`）。**不是所有数据商都用这个约定**——同花顺 iFind 的成功信封是
        `{"code":1,"msg":"success",…}`，用默认判据会把每一次成功都当失败去重试，白翻一倍延迟
        并烧掉并发配额（iFind 免费版只有 2）。给这类源传自己的判据，见
        `mcp_gateway/providers/ifind.py::ifind_business_error`。
        """
        self._session = session  # legacy：注入的会话；owned：None → 延迟/重建
        self._owns = session is None
        self._timeout = timeout
        self._retries = retries
        self._reconnect = reconnect
        self._keepalive_interval = keepalive_interval or 0.0
        self._url = url
        self._headers = headers
        self._session_factory = session_factory
        self._business_error = business_error or _is_business_error
        self._stack: AsyncExitStack | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        # 单飞：保活重建 / call_tool 触发重建 / warm_up 串行化，防重建风暴
        self._reconnect_lock = asyncio.Lock()
        self._closed = False

    # ---- 连接生命周期（owned 模式；legacy 全部直通注入的 session） ----

    def _factory(self):
        """返回一个零参 async CM 工厂（调用一次产出一个 async CM）。测试注入 session_factory
        走前者；真实路径用闭包包住 _streamable_session_factory（它本身是 asynccontextmanager 装饰的
        函数——直接返回调用结果会让调用方再「调用一个 CM」，触发 AsyncContextDecorator TypeError）。"""
        if self._session_factory is not None:
            return self._session_factory
        if self._url is None:
            raise RuntimeError("MCPToolProvider 需要注入 session、url 或 session_factory")
        return lambda: _streamable_session_factory(self._url, headers=self._headers, timeout=self._timeout)

    async def _restart(self) -> None:
        """重建一条全新会话。半截上下文显式清理：enter_async_context 失败/取消不会自动回滚已进入的
        资源（读/写流、半初始化 ClientSession），必须 aclose 防止泄漏。"""
        async with self._reconnect_lock:
            if self._session is not None or self._closed:
                return
            stack = AsyncExitStack()
            try:
                session = await stack.enter_async_context(self._factory()())
            except BaseException:
                # 连接到一半被取消/失败：让 stack 关闭已进入的资源（清理期间被再次取消 → 不覆盖原异常），再原样抛
                with suppress(BaseException):
                    await stack.aclose()
                raise
            if self._closed:
                # 建连期间被 close()：不再挂到 provider 上，直接关掉刚建好的会话（2026-09-07 审查：
                # 否则 close 后仍留下活会话 + 自启保活任务，连接泄漏）
                with suppress(Exception):
                    await stack.aclose()
                return
            self._stack = stack
            self._session = session
            self._start_keepalive()

    async def _ensure_connected(self) -> Any:
        """保证有可用会话：owned 且未连 → 重建；已 close / legacy 空注入 → RuntimeError。"""
        if self._closed:
            raise RuntimeError("MCPToolProvider has been closed")
        if not self._owns:
            return self._session
        if self._session is None:
            await self._restart()
        return self._session

    async def _invalidate(self, expect: Any = _NO_EXPECT) -> None:
        """仅关闭当前会话并清空引用；保活任务每轮现读 _session，跨失效存活。

        expect != _NO_EXPECT 时只拆「正是该会话」的连接（身份检查）——保活探测的失败是
        针对它 ping 的那个 session；若期间 call_tool 已重建出新会话，误拆会把刚建好的
        连接杀掉引发连锁重建（2026-09-07 审查）。
        """
        if expect is not _NO_EXPECT and self._session is not expect:
            return
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            with suppress(Exception):
                await stack.aclose()

    # ---- 保活（防断联）：周期 ping，断线即重建 ----

    def _start_keepalive(self) -> None:
        if self._owns and self._keepalive_interval > 0 and self._keepalive_task is None and not self._closed:
            self._keepalive_task = asyncio.create_task(self._keepalive_loop(), name="mcp-keepalive")

    async def _keepalive_loop(self) -> None:
        # 循环内不吞 BaseException：CancelledError 透传，close() 的 cancel 立即生效
        while not self._closed:
            await asyncio.sleep(self._keepalive_interval)
            session = self._session
            if session is None:
                continue
            try:
                await session.send_ping()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 保活只做健康判定，不抛出
                if _is_transport_failure(exc):
                    logger.warning("MCP 保活探测失败，重建会话: %s", exc)
                    await self._invalidate(session)
                    # 后台持续重建直到连上（或连接被永久关闭）
                    while self._session is None and not self._closed:
                        try:
                            await self._restart()
                            break
                        except Exception:  # noqa: BLE001 - 重连失败继续退避重试
                            await asyncio.sleep(min(self._keepalive_interval, 30.0))
                else:
                    # 协议级错误（如服务端未实现 ping）→ 服务器活着，不重建
                    logger.debug("MCP 保活探测被服务端拒绝（协议级）: %s", exc)

    async def close(self) -> None:
        """幂等关闭：停保活 + 关当前会话。legacy（注入会话）无操作，绝不关闭测试的假会话。

        持 _reconnect_lock 与在途重建互斥（2026-09-07 审查：原先不持锁，在途 _restart 会在
        close 清空后再挂上新会话并自启保活，泄漏连接）。
        """
        self._closed = True
        task, self._keepalive_task = self._keepalive_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        async with self._reconnect_lock:
            await self._invalidate()

    async def warm_up(self) -> None:
        """热启动：确保连接 + 拉一次工具清单（复用 _log_registered_tools 的启动观测打印）。"""
        await self._ensure_connected()
        await self.list_tools()

    # ---- 查询/调用（与旧版契约一致；仅增加「断线重建」分支） ----

    async def list_tools(self) -> list[ToolSpec]:
        """拉工具清单：与 call_tool 一致的『传输失败先作废会话再重试』语义（owned 模式）。

        保活/其它调用并发 _invalidate 时，正在进行的 session.list_tools() 可能读到已关闭的
        anyio 流（ClosedResourceError/BrokenResourceError，2026-09-07 事故），重试一次刚好经
        _ensure_connected 拿到新会话；重试仍失败则原样抛出（list_tools 没有可安全折叠的『业务失败』
        形态，交给上层统一走错误路径，不伪造空清单）。
        """
        last_exc: Exception | None = None
        # max(1)：retries=0 表示「不重试」= 仍尝试一次；range(0) 会直接落到 raise None（TypeError，2026-09-07 审查）
        for attempt in range(max(self._retries, 1)):
            session: Any = None
            try:
                session = await self._ensure_connected()
                result = await session.list_tools()
            except Exception as exc:
                last_exc = exc
                if session is not None and self._owns and self._reconnect and _is_transport_failure(exc):
                    await self._invalidate(session)
                if attempt < self._retries - 1:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                raise
            else:
                specs = [to_tool_spec(t) for t in result.tools]
                _log_registered_tools(specs)
                return specs
        raise last_exc  # pragma: no cover - 循环内总会 return 或 raise

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """调用工具：带读超时 + 重试 + 断线即重建。

        重试『抛出的异常』与『业务失败』（isError 或返回 JSON 的 code!=0）；重试耗尽后：
          - 权限类失败（msg 含 积分/权限/无权限/提升 等）→ 返回友好提示（is_error=False），让 LLM 如实转述；
          - 其它业务失败 → 保留原始结果；
          - 异常耗尽 → 返回带 "after N tries" 的错误信息（is_error=True）。
        传输层异常（owned 模式）会先作废当前会话，下一次尝试自动重建——断线自愈且不改变失败语义。
        """
        args = arguments or {}
        last_exc: Exception | None = None
        last_text: str | None = None
        last_was_error: bool = False
        last_permission: bool = False
        # max(1)：retries=0 =「不重试」= 仍尝试一次（同 list_tools）
        for attempt in range(max(self._retries, 1)):
            session: Any = None
            try:
                session = await self._ensure_connected()
                result = await session.call_tool(
                    name, args, read_timeout_seconds=timedelta(seconds=self._timeout)
                )
            except Exception as exc:  # noqa: BLE001 - 工具层吞掉一切，转 is_error 保循环存活
                last_exc = exc
                last_text = None
                last_was_error = True
                last_permission = False
                if session is not None and self._owns and self._reconnect and _is_transport_failure(exc):
                    # 断线：作废会话（带身份检查），下一次尝试经 _ensure_connected 正好重建一次
                    await self._invalidate(session)
                if attempt < self._retries - 1:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                return ToolResult(
                    content=f"Error calling {name} after {self._retries} tries: {exc}", is_error=True
                )
            parts = [b.text for b in result.content if getattr(b, "type", None) == "text"]
            text = "\n".join(parts) or "OK"
            # 成功：既非 isError 也非业务失败（判据可按源覆盖，见 business_error）
            if not result.isError and not self._business_error(text):
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


def gateway_business_error(text: str) -> bool:
    """网关聚合连接专用的业务失败判据：JSON dict 且 `code` **不属于** {0, 1}。

    为什么默认判据（`_is_business_error`，`code != 0`）在这条连接上是错的：
    网关把五个源聚合到**一条** MCP 连接上，而各源的「成功」约定互相矛盾——
    Tushare `code:0` 成功，而 **iFind `code:1` 成功**（`mcp_gateway/providers/ifind.py`）。
    默认判据会把每一次成功的 iFind 调用判成业务失败并重试一遍：
    实测同一次调用（输出逐字节相同）retries=2 用 6.20s、retries=0 用 0.65s，
    **9.5× 放大**，还把 iFind「套餐硬限并发 2」的配额双倍烧掉。

    网关侧虽然已给 iFind 源传了 `business_error`，但那只管网关自己到 iFind 的那一跳；
    demomcp → 网关这一跳是另一条连接、另一个 `MCPToolProvider`，必须单独给判据。

    刻意**不改** `_is_business_error`：它被 `tests/test_mcp_retry.py` 用
    `{"code":1,"msg":"参数缺失"}` 当作业务失败的标准样例锁定，而在 Tushare 语义下那是对的。
    同一段文本的含义取决于它来自哪个源，所以这里是「多一个判据」而不是「改判据」。

    接新数据源时若它的成功码不是 0/1，记得把它加进这里的白名单
    （否则它的每次成功都会被静默重试一遍——不报错、只是慢一倍且烧配额，很难发现）。
    """
    body = _parse_result(text)
    if body is None:
        return False
    code = body.get("code", 0)
    if not isinstance(code, (int, float)) or isinstance(code, bool):
        return False
    return code not in (0, 1)


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
    url: str,
    *,
    timeout: float = 30.0,
    retries: int = 2,
    headers: dict[str, str] | None = None,
    keepalive_interval: float | None = None,
    reconnect: bool = True,
    business_error: Callable[[str], bool] | None = None,
) -> AsyncIterator[MCPToolProvider]:
    """经 HTTP 连接独立 mcp_server 并复用一条 MCP 连接，整个 asyncio 会话内有效。

    headers 供需要自定义鉴权头的端点使用（如万得 Wind 的 `Authorization: Bearer <key>`）。
    显式传 `sse_read_timeout=timeout`，避免 `mcp>=1.28` 默认 300s 的挂起阈值覆盖读超时预期。
    进 CM 即连接（供 web 热启动）；断线后 provider 自持 url 可自动重建；退出时 close（停保活 + 关会话）。
    """
    provider = MCPToolProvider(
        url=url,
        headers=headers,
        timeout=timeout,
        retries=retries,
        keepalive_interval=keepalive_interval,
        reconnect=reconnect,
        business_error=business_error,
    )
    try:
        await provider._ensure_connected()
        yield provider
    finally:
        await provider.close()
