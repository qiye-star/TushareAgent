"""网关顶层 ASGI 组装：`/mcp` 挂 MCP streamable-http 端点，`/admin/*` 挂管理 REST 路由，同一个端口。

启动时：`GatewaySettings()` 读配置 → `build_sources()` 组源 → `GatewayToolProvider` 聚合 →
`StreamableHTTPSessionManager` 包成 MCP server；lifespan 里 `gateway.start()`（逐源热启动，指数退避、
互不阻塞——一个源连不上不耽误其它源可用）+ 进入 session manager 的 `.run()` 上下文（起内部 task group，
`handle_request` 在此之前不可用）。

`/mcp` 挂载用一个薄封装函数而非直接挂 `session_manager.handle_request`：session_manager 是在 lifespan
里才构造出来的（依赖运行时配置），而 `app.mount()` 必须在模块加载时就注册路由；薄封装在**每次请求**时
才去读 `app.state.session_manager`，这时 lifespan 早已跑完，两者顺序不冲突。

运行：`uv run uvicorn mcp_gateway.app:app --host 0.0.0.0 --port 8766`（默认 host/port 见 config.py，
可用 GATEWAY_HOST/GATEWAY_PORT 覆盖；uvicorn 命令行的 --host/--port 优先级更高，容器里用命令行传）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI

from mcp_gateway import admin
from mcp_gateway.config import GatewaySettings
from mcp_gateway.mcp_endpoint import build_session_manager
from mcp_gateway.pool import GatewayToolProvider
from mcp_gateway.sources import build_sources

logger = logging.getLogger("mcp_gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = GatewaySettings()
    problems = settings.config_problems()
    for problem in problems:
        # 配置没配好不崩进程（restart 策略下会变重启风暴），但要大声说出来；/admin/health 同样带上
        logger.error("[配置问题] %s", problem)
    app.state.config_problems = problems
    gateway = GatewayToolProvider(build_sources(settings), data_dir=settings.data_dir)
    session_manager = build_session_manager(gateway)
    app.state.settings = settings
    app.state.gateway = gateway
    app.state.session_manager = session_manager

    gateway.start()
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(session_manager.run())
        try:
            yield
        finally:
            await gateway.stop()


app = FastAPI(title="MCP Gateway", lifespan=lifespan)
app.include_router(admin.router)


async def _mcp_asgi(scope: Any, receive: Any, send: Any) -> None:
    await app.state.session_manager.handle_request(scope, receive, send)


app.mount("/mcp", _mcp_asgi)
