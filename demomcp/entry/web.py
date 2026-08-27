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
from demomcp.providers.tools.mcp import mcp_tool_provider
from demomcp.providers.tools.stocks import StockToolProvider
from demomcp.rag.schemas import RetrievalPlan

# React 前端构建产物输出到 web/dist（见 web/vite.config.ts 的 build.outDir）。
# 开发时用 `cd web && npm run dev`（Vite 代理到本服务）；生产由本静态挂载同源服务 dist。
WEB_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


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


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    store: ChatHistoryStore = app.state.store
    settings: Settings = app.state.settings
    session_id = req.session_id or uuid.uuid4().hex
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
            async with mcp_tool_provider(
                settings.tushare_mcp_url, timeout=settings.mcp_timeout, retries=settings.mcp_retries
            ) as tools:
                # 语义工具层：包裹底层 MCP，只暴露双票限定语义工具（隐藏通用 query/list_apis/get_api_info）
                stock_tools = StockToolProvider(tools, config=settings)
                agent = Agent(llm=llm, tools=stock_tools, config=settings)
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
        try:
            while True:
                kind, data = await queue.get()
                if kind == "__end__":
                    break
                yield _sse(kind, data)
        finally:
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
