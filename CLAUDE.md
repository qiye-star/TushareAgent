# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

demo-mcp 是一个**独立**的 LLM+MCP 数据对话助手（DeepSeek · MCP · Web/CLI），与仓库根目录的 tushare-data 代理应用是**两套东西**。用户用自然语言提问，内置智能体自行决定调用哪个工具、经 MCP server 从**外部** Tushare 数据代理（`TUSHARE_PROXY_URL`）取数，再总结成中文回答。

- 自带 uv 环境：`uv sync` 建项目自己的 `.venv`；自带 Docker（`docker compose up`）；自带内置 MCP server（`mcp_server/server.py`）。
- 数据源是你另行提供的一个 Tushare 代理（暴露 `GET /api/registry`、`POST /api/query`）；demo-mcp 自身不 import 任何父应用代码，也不共享父仓库的 venv。

## 常用命令

```bash
# 安装（生成 .venv + uv.lock）
uv sync                          # 或 uv sync --no-dev（容器/只装运行时）

# 运行
uv run demomcp.entry.cli                          # CLI 对话
uv run uvicorn demomcp.entry.web:app --port 8010  # Web（SSE 流式）

# 测试（Windows exe 在 .venv/Scripts，Linux/容器在 .venv/bin）
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py::test_full_loop_discover_then_query
.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -k max_iterations

# 静态检查
.venv/Scripts/ruff.exe check demomcp tests scripts
PYRIGHT_PYTHON_NODE_VERSION=22.23.2 .venv/Scripts/pyright

# 真实端到端（需数据代理在跑 + 真 DS_API_KEY/TUSHARE_API_KEY）
uv run scripts/smoke_e2e.py
```

> Windows venv 无 pip；一律用 `uv`（`uv sync`/`uv run`/`uv pip install`）。

## 架构（big picture）

分层严格、依赖单向：`entry → agents → interfaces`；`providers → interfaces`；`config` 为叶子；`db` 被 entry 使用。

```
interfaces  类型 + ToolProvider / LLMClient 两协议（纯契约，无实现）
agents      Agent 手动 agentic loop + 工具注册（只依赖 interfaces）
providers   tools: mcp/fake；llm: deepseek/mock —— 可插拔适配器
config      Settings + PROJECT_ROOT + build_stdio_params
db          SQLAlchemy 2.0 异步：ChatMessage（可读日志）+ ChatTurn（精确恢复）
entry       cli.py / web.py（SSE 流式 + /api/sessions）
mcp_server  内置数据 server（子进程运行，把代理暴露成 list_apis/get_api_info/query）
```

**核心链路**：`Agent.run(history, on_text/on_thinking/on_tool)` → `LLMClient.chat(stream…)`（DeepSeek 流式 + tool_calls + reasoning_content）→ `MCPToolProvider.call_tool`（重试+超时）→ 内置 `mcp_server/server.py`（stdio 子进程）→ 外部代理。结果归一化为 `ChatResponse`，循环到 `end_turn` 为止；错误一律转 `is_error` ToolResult，循环永不崩。

**理由关键**：`Agent` 只依赖 `LLMClient` + `ToolProvider` 两个协议；消息帧（`assistant_message` / `tool_results_messages`）由 LLM 实现自己渲染 → 换后端不改循环。`on_text`/`on_thinking` 已在 agent 层流式吐出内容与 reasoning，`on_tool` 抛出每次工具调用（供前端做「思考轨迹」）。

**Web**：`POST /chat` 走 SSE（`thinking/text/tool/done/error`），`lifespan` 复用 `store`；`GET|DELETE /api/sessions`、`GET /api/sessions/{id}`；**恢复**用 `ChatTurn.messages_json`（每轮完整 OpenAI 消息，累计式）精确还原 tool_calls/tool_call_id。

## 关键坑（改了会踩）

- **`mcp` 钉 `>=1.28,<2`**（FastMCP）；`ClientSession.call_tool` 的 `read_timeout_seconds` 参数是 **`timedelta`，不是秒**（传 `timedelta(seconds=…)`）。
- **内置 MCP server 是子进程**（`mcp_server/server.py` 的 `mcp.run()`），demo-mcp **不 import** 它 → 与 `mcp` SDK 包无命名冲突；`MCP_PYTHON` 留空 = `sys.executable`（venv/容器解释器，自带 mcp+httpx）。
- `config/env.py`：`PROJECT_ROOT = parents[2]`；`MCP_SERVER_PATH` 默认 `PROJECT_ROOT/mcp_server/server.py`（自包含，留空即可，无需填外部路径）。
- **`create_all` 不会 ALTER 既有表**：改了 `models.py` 后要删掉旧的 `demo.db`（schema 漂移会 500）。
- **SSE 用 POST + `ReadableStream`** 解析，不是 `EventSource`（无法 POST body）。前端 `marked`+`DOMPurify` 先 sanitize 再渲染（LLM 输出不可信）。
- 代理工具返回 `{code,msg,row_count,data}`；`code!=0`（如「无权限需提升积分」）是**非错误**结果，LLM 读友好 `msg` 自行调整；**只对抛出的异常**重试，`isError` 业务结果不重试。
- `Agent.run` **无条件**把 assistant 轮追加进 `messages`（最终答复也在历史里），多轮上下文靠它。
- 前端会流式重渲染 Markdown（每帧 `DOMPurify.sanitize(marked.parse(累计文本))`），别在回调里做重活。

## 关键文件

`demomcp/agents/agent.py`（loop + on_tool）、`demomcp/providers/llm/deepseek.py`（流式/工具/回传 reasoning_content）、`demomcp/providers/tools/mcp.py`（重试+超时 + stdio 工厂）、`demomcp/entry/web.py`（SSE + sessions API + lifespan store）、`demomcp/db/{models,store}.py`、`demomcp/config/{settings,env}.py`、`mcp_server/server.py`.
