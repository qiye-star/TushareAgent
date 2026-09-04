"""入口层 · Web：SSE 流式聊天 + 会话日志 API，并托管聊天页。

- POST /chat 返回 SSE（thinking/text/tool_call/tool_result/done/error），逐字流式，并把会话历史落到 demo-mcp 自己的库。
- GET /api/sessions、GET /api/sessions/{id} 供侧栏历史会话 / 日志查看 / 恢复。
- store 在 lifespan 里复用（避免每请求建表/建引擎）。

运行（demo-mcp 自带 .venv，先 uv sync）：
    python -m uvicorn demomcp.entry.web:app --port 8010
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from demomcp.agents.agent import Agent
from demomcp.config.logging import configure_logging, get_logger, log_chat_turn
from demomcp.config.settings import Settings
from demomcp.db.store import ChatHistoryStore, build_store
from demomcp.providers.llm.deepseek import DeepSeekLLMClient
from demomcp.providers.tools.wind import agent_tool_provider
from demomcp.rag.schemas import RetrievalPlan

# React 前端构建产物输出到 scripts/web/dist（见 scripts/web/vite.config.ts 的 build.outDir）。
# 开发时用 `cd scripts/web && npm run dev`（Vite 代理到本服务）；生产由本静态挂载同源服务 dist。
WEB_DIR = Path(__file__).resolve().parents[2] / "scripts" / "web" / "dist"

# Agent 每轮可容忍的「完全静默」上限：超过即视为卡死（外部调用可能在 asyncio 层绕过自身超时，
# 导致 queue 再无事件、task.cancel() 兜底也走不到）。到点就 cancel 并发 error/__end__，
# 保证 SSE 一定收敛、前端不再无限转圈。须大于单次 LLM 最长静默（deepseek.py 读超时 180s + 余量），
# 避免误杀正常长思考（deepseek-reasoner 非流式节点可能长时间无事件）。
AGENT_IDLE_TIMEOUT = 240.0


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
    try:
        yield
    finally:
        await store.dispose()


app = FastAPI(title="Tushare demo-mcp", lifespan=lifespan)
logger = get_logger("entry.web")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    model: str | None = None  # 前端「深度思考」可传 deepseek-reasoner


def _sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _settle(task: asyncio.Task[Any]) -> None:
    """给已 cancel 的任务一段退出时间（跑 __aexit__ 释放连接）；不给无限时间，它自己也可能是卡源。"""
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001 - best-effort 收尾，清理期异常不阻断 SSE 收敛
        logger.debug("agent 取消后未在 5s 内完全退出: %r", exc)


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

    async def _run_agent() -> None:
        llm = DeepSeekLLMClient(
            api_key=settings.ds_api_key,
            base_url=settings.ds_base_url or None,
            model=(req.model or settings.ds_model) or None,
        )
        try:
            async with agent_tool_provider(settings) as tools:
                # 直接使用原始 MCP provider：LLM 看到服务端暴露的全部工具（list_apis/get_api_info/query + 各接口工具），可查任意标的任意接口
                agent = Agent(llm=llm, tools=tools, config=settings)
                result = await agent.run(
                    req.message,
                    history=history,
                    on_text=on_text,
                    on_thinking=on_thinking,
                    on_tool=on_tool,
                    on_process=on_process,
                )
            await store.append(session_id, "assistant", result.final_text)
            await store.append_turn(session_id, result.messages)
            structured = result.structured or {}
            blob = {
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


def _mount_static() -> None:
    if (WEB_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


_mount_static()
