"""入口层 · Web：SSE 流式聊天 + 会话日志 API，并托管聊天页。

- POST /chat 返回 SSE（thinking/text/tool_call/tool_result/done/error），逐字流式，并把会话历史落到 demo-mcp 自己的库。
- GET /api/sessions、GET /api/sessions/{id} 供侧栏历史会话 / 日志查看 / 恢复。
- store 在 lifespan 里复用（避免每请求建表/建引擎）。

运行（demo-mcp 自带 .venv，先 uv sync）：
    python -m uvicorn demomcp.entry.web:app --port 8010
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from demomcp.agents.agent import Agent
from demomcp.config.logging import configure_logging, get_logger, log_chat_turn
from demomcp.config.mcp_toggle import load_mcp_enabled, save_mcp_enabled
from demomcp.config.settings import Settings
from demomcp.config.skill_toggle import (
    is_skill_enabled,
    load_skill_toggles,
    save_skill_enabled,
)
from demomcp.db.store import ChatHistoryStore, build_store
from demomcp.graph.skill_loader import CAPABILITY_NOTE
from demomcp.graph.skills import LIBRARY, get_skill
from demomcp.interfaces.tool_provider import ToolProvider
from demomcp.providers.llm.deepseek import DeepSeekLLMClient
from demomcp.providers.tools.mcp import gateway_business_error, mcp_tool_provider
from demomcp.providers.tools.null import NullToolProvider
from demomcp.rag.schemas import RetrievalPlan

# React 前端构建产物输出到 scripts/web/dist（见 scripts/web/vite.config.ts 的 build.outDir）。
# 开发时用 `cd scripts/web && npm run dev`（Vite 代理到本服务）；生产由本静态挂载同源服务 dist。
WEB_DIR = Path(__file__).resolve().parents[2] / "scripts" / "web" / "dist"

# Agent 每轮可容忍的「完全静默」上限：超过即视为卡死（外部调用可能在 asyncio 层绕过自身超时，
# 导致 queue 再无事件、task.cancel() 兜底也走不到）。到点就 cancel 并发 error/__end__，
# 保证 SSE 一定收敛、前端不再无限转圈。须大于单次 LLM 最长静默（deepseek.py 读超时 180s + 余量），
# 避免误杀正常长思考（deepseek-reasoner 非流式节点可能长时间无事件）。
AGENT_IDLE_TIMEOUT = 240.0

# 工具池（Tushare + 万得，共 8 条 MCP 会话）周期重建间隔：量级对齐 db/store.py 的 pool_recycle=1800，
# 作为最终安全网（网络抖动/远端重启导致的会话半死不至于要靠手工重启进程才能恢复）——
# 日常自愈已前移到每会话层：保活 ping（mcp.py _keepalive_loop）+ 传输层断线自动重建。
TOOLS_RECYCLE_INTERVAL = 1800.0

# 工具池热启动失败重试的指数退避（秒）：首次 5s，之后翻倍，上限 60s。
TOOL_HOT_START_BACKOFF_BASE = 5.0
TOOL_HOT_START_BACKOFF_MAX = 60.0


@dataclass
class _ToolPool:
    """工具池租约：provider + 进出栈；users = 在途请求数（回收后等归零才关，绝不凭固定 idle 宽限）。"""

    provider: ToolProvider
    stack: AsyncExitStack
    users: int = 0
    retired: bool = False


def _tools_context(settings: Settings):
    """唯一的取数连接：作为 MCP 客户端连独立部署的网关（`MCP_GATEWAY_URL`）。

    本进程**不再**直连任何数据源——Tushare 官方 MCP / 万得 Wind / 未来更多源都由 `mcp_gateway/`
    那个独立进程持有并按源开关，所以启动 demomcp 不会带起任何上游 MCP 连接。网关没起来时
    `_hot_start_tools` 退避重试、`/chat` 侧懒连失败也只是本轮无工具，进程照常活着（自愈路径）。
    复用 `MCPToolProvider` 已有的保活 ping + 断线自动重建，零新代码。
    """
    return mcp_tool_provider(
        settings.mcp_gateway_url,
        timeout=settings.mcp_timeout,
        retries=settings.mcp_retries,
        keepalive_interval=settings.mcp_keepalive_interval or None,
        # 网关一条连接上流着五个源的混合信封，各源「成功码」互相矛盾
        # （Tushare code:0 成功 / iFind code:1 成功）→ 必须用聚合判据，
        # 否则每次 iFind 成功都被判失败并重试（实测 0.65s → 6.20s，并双倍烧 iFind 并发配额）
        business_error=gateway_business_error,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    store = build_store(settings.effective_database_url)
    await store.init()
    configure_logging(
        settings.log_level, Path(settings.log_file), settings.log_max_bytes, settings.log_backup_count
    )
    app.state.settings = settings
    app.state.store = store
    # MCP 全局运行时开关：持久化在 data/settings/mcp_toggle.json，跨重启保留；关闭时启动阶段
    # 不建任何 MCP 连接（见下方 hot_start 条件），/chat 改注入 NullToolProvider（见 chat()）。
    app.state.mcp_enabled = load_mcp_enabled()
    # 工具池：全程复用，避免 CLAUDE.md 记录的「每条消息重连 8 个 MCP 会话」卡死问题。建池时机 =
    # 热启动：由 _hot_start_tools 后台任务在启动时完成（非阻塞，失败率不拖垮启动）；
    # 冷启动回退：首个 /chat 仍可经 _get_tools 的同一把双检锁懒建（幂等，绝不重复建连）。
    # 不在 lifespan 里 await 的原因是 test_rag_server.py 等 TestClient(app) 测试会触发 lifespan——
    # 阻塞式 eager 连接会逼迫测试联网；后台任务 + TOOL_POOL_HOT_START=false 提供了离线确定性。
    app.state.tools_pool: _ToolPool | None = None
    app.state.tools_retired: list[_ToolPool] = []
    app.state.tools_lock = asyncio.Lock()
    app.state.tools_recycle_task = asyncio.create_task(_recycle_tools_loop(app))
    app.state.tools_hot_start_task = (
        asyncio.create_task(_hot_start_tools(app))
        if settings.tool_pool_hot_start and app.state.mcp_enabled
        else None
    )
    # 快报：手动生成与每日定时任务共用一把锁（409 互斥）；QUICKREPORT_AUTO=false（测试）不建后台任务
    app.state.quickreport_lock = asyncio.Lock()
    app.state.quickreport_task = (
        asyncio.create_task(
            _quickreport_forever(app)
        ) if settings.quickreport_enabled and settings.quickreport_auto else None
    )
    try:
        yield
    finally:
        for task in (
            app.state.tools_recycle_task,
            app.state.tools_hot_start_task,
            app.state.quickreport_task,
        ):
            if task is not None:
                task.cancel()
                # shield 给取消中的任务一段退出时间（镜像 _settle），半截连接由 _restart 的 BaseException 清理兜底
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        # 工具池：当前 + 回收中的全部关闭（回收循环被取消后其 finally 已关旧池——AsyncExitStack 幂等，重复关安全）
        for pool in [app.state.tools_pool, *app.state.tools_retired]:
            if pool is not None:
                with contextlib.suppress(Exception):
                    await pool.stack.aclose()
        app.state.tools_pool = None
        app.state.tools_retired.clear()
        await store.dispose()


app = FastAPI(title="Tushare demo-mcp", lifespan=lifespan)
logger = get_logger("entry.web")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    model: str | None = None  # 前端「深度思考」可传 deepseek-reasoner
    mode: str | None = None  # 前端「快速问答/智能体模式」选择器；"quick" | "agent"，非法值/空回落 agent
    skill: str | None = None  # 前端技能页「快速使用」指定的报告 skill id；未知/停用 → 忽略（不 500）


def _sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _settle(task: asyncio.Task[Any]) -> None:
    """给已 cancel 的任务一段退出时间（跑 __aexit__ 释放连接）；不给无限时间，它自己也可能是卡源。"""
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001 - best-effort 收尾，清理期异常不阻断 SSE 收敛
        logger.debug("agent 取消后未在 5s 内完全退出: %r", exc)


async def _acquire_tools(app: FastAPI) -> ToolProvider:
    """取池 + 租约计数：懒构建 + 双重检查锁（首个请求建好 8 条 MCP 会话后全程复用，不再每条消息重连）。

    锁贯穿整个构建过程（不是只包读/写两端），避免并发首批请求各自重复建连（惊群）——
    这是 rag/runtime.py 现有 _CACHE_LOCK 用法的已知缺陷，这里不重蹈。
    调用方必须配对 `await _release_tools(app, tools)`（计数不足时回收循环不关池，长请求永不中途断连）。

    MCP 全局开关关闭时直接拒绝（不懒建）：`/chat` 已经在开关关闭时改注入 `NullToolProvider`、
    根本不会走到这里；仍会调用本函数的是快报手动端点/定时任务——开关关闭时它们应明确失败，
    而不是静默复用/重连一个用户刚要求断开的连接池。
    """
    if not app.state.mcp_enabled:
        raise RuntimeError("MCP 数据源已停用")
    pool = app.state.tools_pool
    if pool is None:
        async with app.state.tools_lock:
            pool = app.state.tools_pool
            if pool is None:
                stack = AsyncExitStack()
                pool = _ToolPool(
                    provider=await stack.enter_async_context(_tools_context(app.state.settings)),
                    stack=stack,
                )
                app.state.tools_pool = pool
    pool.users += 1
    return pool.provider


async def _release_tools(app: FastAPI, provider: ToolProvider) -> None:
    """归还租约：只减计数；回收中的池由 _recycle_tools_loop 在计数归零后关闭，无需在此关。"""
    pool = app.state.tools_pool
    if pool is not None and pool.provider is provider:
        pool.users = max(0, pool.users - 1)
        return
    for retired in app.state.tools_retired:
        if retired.provider is provider:
            retired.users = max(0, retired.users - 1)
            return


async def _hot_start_tools(app: FastAPI) -> None:
    """工具池热启动：启动即后台建池 + 预热（Tushare 工具清单 / Wind 7 域），失败指数退避直到成功。

    与 /chat 共用 _acquire_tools 的租约：首请求仍可触发懒建、绝不重复建连；本循环同时承担
    「启动期重新启动机制」——远端启动时不可用也能在没有用户流量时自动恢复。
    """
    delay = 0.0
    while True:
        tools = None
        try:
            tools = await _acquire_tools(app)
            warm = getattr(tools, "warm_up", None)  # 鸭式：没有 warm_up 的 provider（含测试 fake）跳过
            if warm is not None:
                await warm()
            logger.info("已连接 MCP 网关 %s（工具池热启动完成）", app.state.settings.mcp_gateway_url)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 热启动非致命：失败记日志并退避重试
            delay = TOOL_HOT_START_BACKOFF_BASE if delay == 0.0 else min(delay * 2, TOOL_HOT_START_BACKOFF_MAX)
            logger.warning(
                "连接 MCP 网关 %s 失败，%.0fs 后重试（网关未启动？）：%s",
                app.state.settings.mcp_gateway_url, delay, exc,
            )
            await asyncio.sleep(delay)
        finally:
            if tools is not None:
                await _release_tools(app, tools)


async def _retire_pool(app: FastAPI, pool: _ToolPool) -> None:
    """把一个池标记退休、等在途请求归零再真正关闭——供「周期重建」与「开关关闭」共用。

    取消/异常也保证关闭（`finally`）；幂等：重复 aclose 安全。调用方需自行把 `pool` 从
    `app.state.tools_pool` 摘下并加入 `app.state.tools_retired`（本函数只负责等待+关闭+移除）。
    """
    logger.info("工具池已退休；等待 %d 个在途请求结束", pool.users)
    try:
        while pool.users > 0:
            await asyncio.sleep(0.5)
    finally:
        with contextlib.suppress(Exception):
            await pool.stack.aclose()
        with contextlib.suppress(ValueError):
            app.state.tools_retired.remove(pool)


async def _recycle_tools_loop(app: FastAPI) -> None:
    """周期性重建工具池（自愈）：新池建好后**持锁**原子切换（2026-09-07 审查：原先无锁交换与
    _acquire_tools 的双检协议交错，可致池被关在 `app.state.tools` 上全局断连 30 分钟）；
    旧池等**在途请求全部结束**（users==0）再关——原来的固定 240s 宽限会在长对话中途关掉
    仍在使用的会话（AGENT_IDLE_TIMEOUT 只限静默、不限请求总长），现在按引用计数收尾。
    循环取消（lifespan 关闭）时 finally 保证旧池也被关闭，不泄漏会话。

    MCP 全局开关关闭期间跳过重建（不偷偷重连用户刚要求断开的连接）。
    """
    while True:
        await asyncio.sleep(TOOLS_RECYCLE_INTERVAL)
        if not app.state.mcp_enabled:
            continue
        new_stack = AsyncExitStack()
        try:
            new_tools = await new_stack.enter_async_context(_tools_context(app.state.settings))
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await new_stack.aclose()
            raise
        except Exception as exc:  # noqa: BLE001 - 重建失败沿用旧池，下一轮再试
            with contextlib.suppress(Exception):
                await new_stack.aclose()
            logger.warning("工具池周期重建失败，沿用旧池：%s", exc)
            continue
        async with app.state.tools_lock:
            old = app.state.tools_pool
            app.state.tools_pool = _ToolPool(new_tools, new_stack)
            if old is not None:
                old.retired = True
                app.state.tools_retired.append(old)
        if old is None:
            continue
        await _retire_pool(app, old)


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    store: ChatHistoryStore = app.state.store
    settings: Settings = app.state.settings
    session_id = req.session_id or uuid.uuid4().hex
    started = time.monotonic()
    logger.info(
        "chat start session=%s model=%s q=%.80s",
        session_id,
        req.model or settings.ds_model,
        req.message,
    )
    history = await store.last_turn_messages(session_id) or []
    await store.append(session_id, "user", req.message)

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    # 本轮累积：service 端在同一回调里收集，供落库为 ChatTurnData（前端重载原样还原）。
    turn: dict[str, Any] = {"thinking": "", "steps": []}

    async def emit(kind: str, data: dict[str, Any]) -> None:
        await queue.put((kind, data))

    async def on_text(text: str) -> None:
        await emit("text", {"text": text})

    async def on_thinking(text: str) -> None:
        turn["thinking"] += text
        await emit("thinking", {"text": text})

    async def on_tool(name: str, arguments: dict[str, Any], result: Any) -> None:
        turn["steps"].append({"kind": "tool_call", "data": {"name": name, "input": arguments}})
        turn["steps"].append(
            {"kind": "tool_result", "data": {"name": name, "content": result.content, "ok": not result.is_error}}
        )
        await emit("tool_call", {"name": name, "input": arguments})
        await emit("tool_result", {"name": name, "content": result.content, "ok": not result.is_error})
        await store.append(session_id, "tool", result.content, is_error=result.is_error)

    async def on_process(kind: str, data: dict[str, Any]) -> None:
        turn["steps"].append({"kind": kind, "data": data})
        await emit("process", {"kind": kind, **data})

    chat_mode = req.mode if req.mode in ("quick", "agent") else "agent"  # 非法值兜底成 agent，不让前端传坏值搞崩图选择
    # 技能页「快速使用」指定的 skill：未知 id 或已被停用 → 忽略（按普通路由跑，不 500 也不假装用了）
    forced_skill = req.skill if (req.skill and get_skill(req.skill) and is_skill_enabled(req.skill)) else None
    if req.skill and not forced_skill:
        logger.warning("忽略无效/已停用的 skill：%s", req.skill)

    async def _run_agent() -> None:
        # 深度思考（deepseek-reasoner）只作用于最终 synthesizer：router/rewrite_query/tool_rag 选工具
        # 三步要么非流式从不展示推理过程（router/rewrite_query），要么是循环体、每轮都调一次（tool_rag）——
        # 套用 reasoner 只会白白拉长延迟，不产出任何用户可见收益。fast_llm 恒用 settings.ds_model；
        # 只有当请求的 model 与之不同（用户开了深度思考）才额外建一个 reasoner client 只喂给 synthesizer。
        fast_model = settings.ds_model or None
        requested_model = (req.model or settings.ds_model) or None
        fast_llm = DeepSeekLLMClient(
            api_key=settings.ds_api_key,
            base_url=settings.ds_base_url or None,
            model=fast_model,
        )
        synth_llm = (
            fast_llm
            if requested_model == fast_model
            else DeepSeekLLMClient(
                api_key=settings.ds_api_key,
                base_url=settings.ds_base_url or None,
                model=requested_model,
            )
        )
        tools = None
        acquired_from_pool = False
        try:
            if app.state.mcp_enabled:
                # 工具池是进程级单例（_acquire_tools，见 lifespan）：首个请求懒建、全程复用，不再每条消息重连 8 个 MCP 会话；
                # 配对 _release_tools 归还租约——回收循环只有等所有在途请求结束才会关旧池。
                tools = await _acquire_tools(app)
                acquired_from_pool = True
            else:
                # MCP 全局开关关闭：不触碰工具池（避免误触发懒建），Agent 退化为纯 LLM 聊天。
                tools = NullToolProvider()
            # 直接使用原始 MCP provider：LLM 看到服务端暴露的全部工具（list_apis/get_api_info/query + 各接口工具），可查任意标的任意接口
            agent = Agent(llm=fast_llm, tools=tools, config=settings, synth_llm=synth_llm)
            result = await agent.run(
                req.message,
                history=history,
                mode=chat_mode,
                forced_skill=forced_skill,
                on_text=on_text,
                on_thinking=on_thinking,
                on_tool=on_tool,
                on_process=on_process,
            )
            await store.append(session_id, "assistant", result.final_text)
            await store.append_turn(session_id, result.messages)
            structured = result.structured or {}
            blob = {
                "created_at": datetime.now(UTC).isoformat(),
                "mode": chat_mode,
                "query": req.message,
                "thinking": turn["thinking"],
                "steps": turn["steps"],
                "answer": result.final_text,
                "sources": structured.get("sources", []),
                "citations": structured.get("citations", []),
                "claims": structured.get("claims", []),
                "metadata": structured.get("metadata"),
                "intent": structured.get("intent"),
                "strategy": structured.get("strategy"),
                "stopped_reason": result.stopped_reason,
                "usage": result.usage,
                "error": None,
            }
            await store.append_turn_data(session_id, blob)
            log_chat_turn(logger, session_id, blob)
            await emit(
                "done",
                {
                    "stopped_reason": result.stopped_reason,
                    "session_id": session_id,
                    "usage": result.usage,
                    "structured": result.structured,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 单轮错误以 SSE error 事件下发并落库定位
            await store.append(session_id, "error", f"{type(exc).__name__}: {exc}")
            blob = {
                "created_at": datetime.now(UTC).isoformat(),
                "mode": chat_mode,
                "query": req.message,
                "thinking": turn["thinking"],
                "steps": turn["steps"],
                "answer": "",
                "sources": [],
                "citations": [],
                "claims": [],
                "metadata": None,
                "intent": None,
                "strategy": None,
                "stopped_reason": "error",
                "usage": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
            await store.append_turn_data(session_id, blob)
            log_chat_turn(logger, session_id, blob)
            await emit("error", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            if acquired_from_pool and tools is not None:
                await _release_tools(app, tools)
            await emit("__end__", {})

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(_run_agent())
        reason: str | None = None
        try:
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=AGENT_IDLE_TIMEOUT)
                except TimeoutError:
                    # agent 静默超时：外部调用卡死且绕过了自身超时 → 掐掉本轮，SSE 收敛成错误，不再无限转圈。
                    task.cancel()
                    await _settle(task)
                    reason = "timeout"
                    yield _sse("error", {"message": "Agent 长时间无进展，本轮已中止，请重试。"})
                    yield _sse("__end__", {})
                    return
                if kind == "__end__":
                    reason = "done"
                    break
                yield _sse(kind, data)
        finally:
            elapsed = time.monotonic() - started
            if reason == "timeout":
                logger.warning("chat end session=%s reason=timeout elapsed=%.1fs", session_id, elapsed)
            elif reason == "done":
                logger.info("chat end session=%s reason=done elapsed=%.1fs", session_id, elapsed)
            else:
                # 未走完 done/timeout（客户端断连或流内异常）也留痕，便于定位卡死/中断请求
                logger.info("chat end session=%s reason=aborted elapsed=%.1fs", session_id, elapsed)
            task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    return await app.state.store.list_sessions_meta()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> list[dict[str, Any]]:
    messages = await app.state.store.get_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="session not found")
    return messages


@app.get("/api/sessions/{session_id}/turns")
async def get_session_turns(session_id: str) -> list[dict[str, Any]]:
    """每轮面向 UI 的完整载荷（thinking/steps/structured），供前端重载还原；无数据返回 []。"""
    return await app.state.store.load_turn_data(session_id)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, bool]:
    await app.state.store.delete(session_id)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# MCP 全局运行时开关：与主链路解耦——关闭后 /chat 注入 NullToolProvider，Agent 退化为纯 LLM 聊天；
# 与 mcp_server/ 的容器化部署无关（那是内置本地代理，独立 docker-compose service，见 docs）。
# ---------------------------------------------------------------------------


def _mcp_status(app: FastAPI) -> dict[str, Any]:
    pool = app.state.tools_pool
    specs = getattr(pool.provider, "_specs", None) if pool is not None else None
    return {
        "enabled": app.state.mcp_enabled,
        "connected": pool is not None,
        "tool_count": len(specs) if specs is not None else None,
    }


@app.get("/api/settings/mcp")
async def get_mcp_settings() -> dict[str, Any]:
    return _mcp_status(app)


class McpSettingsRequest(BaseModel):
    enabled: bool


@app.post("/api/settings/mcp")
async def set_mcp_settings(req: McpSettingsRequest) -> dict[str, Any]:
    """切换全局开关：落盘持久化 + 更新运行时状态；关闭则摘池并后台真正断开，开启则尝试一次性重连。"""
    save_mcp_enabled(req.enabled)
    app.state.mcp_enabled = req.enabled
    if not req.enabled:
        async with app.state.tools_lock:
            old = app.state.tools_pool
            app.state.tools_pool = None
            if old is not None:
                old.retired = True
                app.state.tools_retired.append(old)
        if old is not None:
            asyncio.create_task(_retire_pool(app, old))
    else:
        asyncio.create_task(_reconnect_tools_once(app))
    return _mcp_status(app)


async def _reconnect_tools_once(app: FastAPI) -> None:
    """开关重新打开后的一次性重连尝试：失败仅记日志，不影响开关状态（下次 /chat 仍会懒建）。"""
    tools = None
    try:
        tools = await _acquire_tools(app)
        warm = getattr(tools, "warm_up", None)
        if warm is not None:
            await warm()
    except Exception as exc:  # noqa: BLE001 - 尽力而为，失败留给下次请求懒建
        logger.warning("MCP 开关重新打开后的重连尝试失败：%s", exc)
    finally:
        if tools is not None:
            await _release_tools(app, tools)


# ---- 按源开关：转发到独立部署的 MCP 网关（mcp_gateway/，唯一的取数来源） ----


@app.get("/api/settings/mcp/sources")
async def get_mcp_sources() -> dict[str, Any]:
    """按源状态 + 网关自身的配置问题，一次取回。

    **网关不可达时也返回 200**（`reachable=false` + `error`）：这是运维态信息，不是请求错误——
    前端要能把「网关没起来 / 网关没配好」原样显示给人看，而不是抛个红叉把整块 UI 隐藏掉
    （2026-09-07：上一版用 gateway_configured 隐藏整块，导致「前端根本看不到按源开关」）。
    """
    settings: Settings = app.state.settings
    admin_url = settings.effective_mcp_gateway_admin_url
    out: dict[str, Any] = {
        "gateway_url": settings.mcp_gateway_url,
        "reachable": False,
        "error": None,
        "config_problems": [],
        "sources": [],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{admin_url}/admin/health")
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["reachable"] = True
    out["config_problems"] = body.get("config_problems") or []
    out["sources"] = body.get("sources") or []
    return out


class McpSourceRequest(BaseModel):
    enabled: bool


@app.post("/api/settings/mcp/sources/{source_id}")
async def set_mcp_source(source_id: str, req: McpSourceRequest) -> dict[str, Any]:
    """切换单个上游源：转发到网关（写操作，网关不可达就是真失败 → 502，不做静默降级）。"""
    settings: Settings = app.state.settings
    admin_url = settings.effective_mcp_gateway_admin_url
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{admin_url}/admin/sources/{source_id}", json={"enabled": req.enabled}
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail=f"未知数据源：{source_id}")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"MCP 网关不可达：{exc}") from exc


# ---- 报告技能库（claude-for vendored 语料 → graph/skills.py）：前端「技能」页 ----


def _skill_row(loaded: Any, toggles: dict[str, bool]) -> dict[str, Any]:
    """技能列表行：只放卡片要用的字段（正文 body 单独走详情端点，别塞进列表）。"""
    return {
        "id": loaded.id,
        "name": loaded.name,
        "raw_name": loaded.raw_name,
        "domain": loaded.domain,
        "domain_label": loaded.domain_label,
        "catalog_line": loaded.catalog_line,
        "report_type": loaded.report_type,
        "enabled": toggles.get(loaded.id, True),
        "files_limited": loaded.files_limited,
        "should_rag": loaded.should_rag,
        "source_families": list(loaded.source_families),
    }


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    """技能清单（按域分组由前端做）。技能库被 SKILL_LIBRARY_ENABLED=false 关掉时返回空列表。"""
    toggles = load_skill_toggles()
    return {
        "skills": [_skill_row(ls, toggles) for ls in LIBRARY],
        "domains": [
            {"id": d, "label": next(ls.domain_label for ls in LIBRARY if ls.domain == d),
             "count": sum(1 for ls in LIBRARY if ls.domain == d)}
            for d in dict.fromkeys(ls.domain for ls in LIBRARY)
        ],
    }


@app.get("/api/skills/{skill_id}")
async def get_skill_detail(skill_id: str) -> dict[str, Any]:
    """技能详情：正文 Markdown + 工具名对照 + 能力限制说明（供详情侧滑渲染）。"""
    loaded = next((ls for ls in LIBRARY if ls.id == skill_id), None)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"未知技能：{skill_id}")
    row = _skill_row(loaded, load_skill_toggles())
    row.update(
        {
            "description": loaded.description,
            "body_markdown": loaded.body,
            "tool_mapping": [{"old": o, "new": n} for o, n in loaded.tool_mapping],
            "capability_note": CAPABILITY_NOTE if loaded.files_limited else None,
            "tool_families": list(loaded.tool_families),
        }
    )
    return row


class SkillToggleRequest(BaseModel):
    enabled: bool


@app.post("/api/skills/{skill_id}/toggle")
async def toggle_skill(skill_id: str, req: SkillToggleRequest) -> dict[str, Any]:
    """启用/停用单个技能：停用后不再进 router 清单（现读，无需重启），也不能被「快速使用」强制指定。"""
    loaded = next((ls for ls in LIBRARY if ls.id == skill_id), None)
    if loaded is None:
        raise HTTPException(status_code=404, detail=f"未知技能：{skill_id}")
    save_skill_enabled(skill_id, req.enabled)
    return {"id": skill_id, "enabled": is_skill_enabled(skill_id)}


@app.post("/api/rag/retrieve")
async def rag_retrieve(req: RetrievalPlan) -> dict[str, Any]:
    """RAG 检索端点：供其它进程（CLI/脚本/2nd worker）经 HTTP 取数，不各自打开 Milvus（单进程独占锁）。

    Milvus 由本 web 进程持有；取不到索引（他进程持锁/未建）→ 显式 degraded，不抛 500。
    """
    from demomcp.rag.runtime import build_runtime_retriever
    from demomcp.rag.server import retrieve_from

    retriever: Any = None
    try:
        retriever = await build_runtime_retriever(app.state.settings)
    except Exception:  # noqa: BLE001 - 索引不可开 → 降级返回
        retriever = None
    return await retrieve_from(retriever, req)


@app.get("/api/rag/health")
async def rag_health() -> dict[str, Any]:
    """检索健康度：索引是否可开、chunk 总数（供前端/运维观察）。"""
    from demomcp.rag.runtime import build_runtime_retriever
    from demomcp.rag.server import health_from

    retriever: Any = None
    try:
        retriever = await build_runtime_retriever(app.state.settings)
    except Exception:  # noqa: BLE001 - 索引不可开 → 视为不可用
        retriever = None
    return health_from(retriever)


# ---------------------------------------------------------------------------
# 快报（quickreport）：六段式高频跟踪报告，确定性取数+计算，与 Agent/LLM 主链路解耦
# ---------------------------------------------------------------------------


async def _quickreport_forever(app: FastAPI) -> None:
    """每日快报调度（sleep-until 08:30 Asia/Shanghai + 启动补跑），镜像 _recycle_tools_loop。

    生成与手动端点共用 quickreport_lock（互斥）；工具池按租约获取并归还。
    """
    from demomcp.quickreport.scheduler import run_forever

    await run_forever(
        lambda: _acquire_tools(app),
        release_tools=lambda t: _release_tools(app, t),
        lock=app.state.quickreport_lock,
        stage_timeout=app.state.settings.quickreport_stage_timeout,
        concurrency=app.state.settings.quickreport_concurrency,
    )


@app.get("/api/quickreport/latest")
async def quickreport_latest() -> dict[str, Any]:
    """读取最近一次成功生成的快报（未生成/损坏 → 404，与「已生成但有段缺失」的 200+missing 区分）。"""
    from demomcp.quickreport.store import load_report

    report = load_report()
    if report is None:
        raise HTTPException(status_code=404, detail="快报尚未生成")
    return report


class QuickReportRequest(BaseModel):
    date: str | None = None  # 覆盖报告日（YYYYMMDD）；空 = 自动解析 T-1 交易日


@app.post("/api/quickreport/generate")
async def quickreport_generate(req: QuickReportRequest) -> dict[str, Any]:
    """手动触发生成（与每日任务共用 quickreport_lock 互斥；进行中 → 409）。

    部分降级也算成功（errors/missing 自带说明）；watchlist 配置缺失/损坏 → 422（**先于取工具池**，
    离线/未联网时 422 也能即时判定，不触发 MCP 连接）。
    """
    from demomcp.quickreport.config import ConfigError, WatchlistConfig
    from demomcp.quickreport.server import generate_from

    cfg = WatchlistConfig.load()
    if cfg is None:
        raise HTTPException(status_code=422, detail="watchlist 配置缺失或损坏（data/quickreport/watchlist.json）")
    if app.state.quickreport_lock.locked():
        raise HTTPException(status_code=409, detail="生成任务进行中，请稍候")
    async with app.state.quickreport_lock:
        tools = None
        try:
            tools = await _acquire_tools(app)
            report = await generate_from(
                tools,
                cfg,
                req.date,
                stage_timeout=app.state.settings.quickreport_stage_timeout,
                concurrency=app.state.settings.quickreport_concurrency,
            )
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"MCP 数据源已停用，无法生成快报：{exc}") from exc
        except Exception as exc:
            logger.exception("快报生成失败")
            raise HTTPException(status_code=500, detail=f"生成失败：{exc}") from exc
        finally:
            await _release_tools(app, tools)
    return report


@app.get("/api/quickreport/status")
async def quickreport_status() -> dict[str, Any]:
    """快报健康状态：数据新鲜度 + 各段来源 + 调度信息 + 上次失败原因。

    **纯读**（latest.json / last_error.json / watchlist.json + 本地时钟），
    **不触发任何 MCP 连接**——与 /latest 同样离线安全，前端可以放心按秒级轮询。

    时间戳一律带 UTC 偏移：前端 `lib/format.ts::formatTime` 见到无偏移的串会补 `Z`
    当 UTC 解析（那是为 SQLite CURRENT_TIMESTAMP 准备的），一个不带偏移的北京墙钟串
    会被显示成早 8 小时。`server_time` 不是可选项——「距下次生成还有多久」必须用
    `next_run_at - server_time` 再套到客户端时钟上，否则机器时钟一歪这个看板就在说谎，
    而它恰恰是别的都不可信时你要看的那个东西。
    """
    from datetime import datetime

    from demomcp.quickreport.config import WatchlistConfig, cn_tz
    from demomcp.quickreport.scheduler import next_run_dt
    from demomcp.quickreport.server import load_latest, required_missing, status_from
    from demomcp.quickreport.store import load_last_error

    settings = app.state.settings
    cfg = WatchlistConfig.load()
    report = load_latest()
    now_cn = datetime.now(cn_tz())
    auto = bool(settings.quickreport_enabled and settings.quickreport_auto)
    return {
        **status_from(report),
        "server_time": now_cn.isoformat(timespec="seconds"),
        "auto": auto,
        "running": app.state.quickreport_lock.locked(),
        "config_ok": cfg is not None,
        "schedule": None
        if cfg is None
        else {"hour": cfg.schedule.hour, "minute": cfg.schedule.minute, "tz": cfg.schedule.tz},
        # 未开启定时任务时不给 next_run_at：给了会让前端显示一个永不到来的时刻
        "next_run_at": next_run_dt(now_cn, cfg.schedule).isoformat()
        if (cfg is not None and auto)
        else None,
        "required_sections": list(cfg.required_sections) if cfg else [],
        "missing_required": required_missing(report, cfg.required_sections) if cfg else [],
        "last_error": load_last_error(),
    }


@app.get("/api/quickreport/history")
async def quickreport_history() -> list[dict[str, Any]]:
    """历史快报摘要列表（日期降序）：{date, generated_at, missing, status_ok}。"""
    from demomcp.quickreport.store import list_histories

    return list_histories()


@app.get("/api/quickreport/report/{day}")
async def quickreport_report_by_date(day: str) -> dict[str, Any]:
    """按日读取存档快报（day = YYYY-MM-DD）；不存在 → 404。"""
    from demomcp.quickreport.store import load_report_by_date

    report = load_report_by_date(day)
    if report is None:
        raise HTTPException(status_code=404, detail="该日期无快报存档")
    return report


def _mount_static() -> None:
    if (WEB_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


_mount_static()
