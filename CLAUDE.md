# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

demo-mcp 是一个**独立**的 LLM+MCP 数据对话助手（DeepSeek · MCP · Web/CLI），与仓库根目录的 tushare-data 代理应用是**两套东西**。用户用自然语言提问，内置智能体自行决定调用哪个工具、经 MCP（`TUSHARE_MCP_URL`，Tushare 官方 `api.tushare.pro/mcp`）取数，再总结成中文回答。

- 自带 uv 环境：`uv sync` 建项目自己的 `.venv`；自带 Docker（`docker compose up`）；连接 Tushare 官方 MCP（`TUSHARE_MCP_URL`）；内置 `mcp_server/server.py` 默认停用、仅后备。
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

# RAG 离线评估（默认合成语料，不联网；--corpus 对真实 PDF，--gold/--top-k/--no-real 可选；真实 PDF 需 --extra rag-full）
uv run scripts/eval_rag.py
uv run scripts/eval_rag.py --corpus "docs/比亚迪：2025年年度报告.pdf" --gold tests/rag_golden/qa_real.yaml

# （可选）RAG 真实后端依赖：uv sync --extra rag-full

# 真实端到端（需真 DS_API_KEY + .env 的 TUSHARE_MCP_URL 指向官方 MCP 且 token 有效）
uv run scripts/smoke_e2e.py
```

> Windows venv 无 pip；一律用 `uv`（`uv sync`/`uv run`/`uv pip install`）。

## 架构（big picture）

分层严格、依赖单向：`entry → agents → interfaces`；`providers → interfaces`；`config` 为叶子；`db` 被 entry 使用。

```
interfaces  类型 + ToolProvider / LLMClient 两协议（纯契约，无实现）
agents      Agent 薄壳 = LangGraph 四节点编排（router/tool_rag/synthesizer/fallback，只依赖 interfaces）
providers   tools: mcp/fake（+ stocks 语义层）；llm: deepseek/mock —— 可插拔适配器
rag         财报知识库（解析/混合检索/引用），独立于主链路、离线优先；retriever.py 目前为空实现占位，仅 eval_rag/测试使用
config      Settings + PROJECT_ROOT（MCP URL / DISCLAIMER / 语义工具 stock_* + RAG rag_*）
db          SQLAlchemy 2.0 异步：ChatMessage（可读日志）+ ChatTurn（精确恢复）
entry       cli.py / web.py（SSE 流式 + /api/sessions）
mcp_server  内置数据 server（默认停用，仅后备；自建 streamable-http + 每接口一个工具，供本地/离线代理用）
```

**核心链路**：`Agent.run(history, on_text/on_thinking/on_tool)` → LangGraph 四节点 `router`（意图分类 + 越界判断）→ `tool_rag`（LLM 选工具 + `ToolProvider.call_tool`；**RAG 暂未接入**——`GraphState.rag_chunks` 是预留字段、图内不调用任何 retriever）→ `synthesizer`（整合 + 引用 + 免责声明）／越界或无证据走 `fallback`。节点内 `LLMClient.chat(stream…)`（DeepSeek 流式 + tool_calls + reasoning_content）透传回调；工具经语义工具层（StockToolProvider）包裹 `MCPToolProvider.call_tool`（重试+超时）→ 官方 MCP（`TUSHARE_MCP_URL`）。结果归一化为 `AgentResult`；错误一律转 `is_error` ToolResult，图永不崩。

**理由关键**：`Agent` 只依赖 `LLMClient` + `ToolProvider` 两协议；图在 `demomcp/graph` 内、由 `build_research_graph(llm, tools, tool_defs, …)` 闭包注入，节点经 `config.configurable` 读回调。消息帧（`assistant_message` / `tool_results_messages`）由 LLM 实现渲染 → 换后端不改图。`on_text`/`on_thinking` 在节点内流式吐出，`on_tool` 在 tool_rag 内逐次抛出（供前端做「思考轨迹」）。

**RAG（`demomcp/rag/`）**：针对 A 股年报 PDF 的问答型 RAG，**离线优先、独立于主链路**（不进 `entry→agents→interfaces`）。摄取：`pdf_parser`（PyMuPDF 解析 → 文本/图片/表格块）→ `section_tree`（字号聚类+编号正则检测章节、去目录页）→ `segments`（块归类、图片交 `captioner`、表格 `table_split` 行分块）→ `chunking`（句感知稠密分块 + 表格独立成块）→ `embedder`（`HashingEmbedder` 默认 / `ApiEmbedder` 走 OpenAI 兼容 `/embeddings`）+ `store`（默认 `InMemoryVectorStore`）& `bm25`（jieba 词元）。检索：`query_build` → `hybrid_retriever`（**三路 RRF**：节稠密+节 BM25+块稠密 → 按 `(doc,section)` 聚类 → top 节 → 重排池 → `reranker` → 阈值截断+`top_k`）→ `RagChunk`。引用：`citing` 确定性生成内联标记与参考列表（不编页码）。默认 `rag_use_real=False` → 纯 Python/确定性；真实后端需 `uv sync --extra rag-full` + `RAG_USE_REAL=true` + `RAG_EMBEDDING_API_KEY`。**尚未接入实时图**：`rag/retriever.py` 仍是 `NullRetriever`（`retrieve(query: str)` 恒返回 `[]`），与真实接口 `RagRetriever.retrieve(plan: RetrievalPlan)` 签名不同——真实入口是 `hybrid_retriever.build_retriever`，目前只被 `scripts/eval_rag.py`/测试使用。设计蓝图见 `docs/RAG_FINANCE.md`、`docs/ARCHITECTURE.md`（属前瞻，与实测有出入）。语义工具层 `StockToolProvider`：硬 allowlist 仅 `002594.SZ`/`300750.SZ`（`DEMO_STOCKS`），`StockInputError`/`StockBusinessError`→`is_error=False`（LLM 自纠）、`StockFetchError`→`is_error=True`；real-time 不可得回退 `daily`+`daily_basic`（`stocks.py`）。

**Web**：`POST /chat` 走 SSE（`thinking/text/tool_call/tool_result/done/error`，`tool_call{name,input}` → `tool_result{content,ok}`，前端据此渲染默认展开的工具卡），`ChatRequest` 支持可选 `model`（前端「深度思考」开关传 `deepseek-reasoner`，从而真正流出 `thinking`/reasoning_content）；`lifespan` 复用 `store`；`GET|DELETE /api/sessions`、`GET /api/sessions/{id}`；**恢复**用 `ChatTurn.messages_json`（每轮完整 OpenAI 消息，累计式）精确还原 tool_calls/tool_call_id。

## 关键坑（改了会踩）

- **`mcp` 钉 `>=1.28,<2`**（FastMCP）；`ClientSession.call_tool` 的 `read_timeout_seconds` 参数是 **`timedelta`，不是秒**（传 `timedelta(seconds=…)`）。
- **默认直连 Tushare 官方 MCP**：`tushare_mcp_url`（`TUSHARE_MCP_URL`）指向 `https://api.tushare.pro/mcp/?token=...`（token 放 URL query），`MCPToolProvider` 经 `streamablehttp_client(url)` 连接，工具由服务器暴露、`list_tools()` 自动发现并打印清单。内置 `mcp_server/` 默认停用（自建时会 `mcp.run(transport="streamable-http")`、读 `MCP_SERVER_HOST/PORT` 与 `TUSHARE_PROXY_*`），demo-mcp **不 import** 它 → 与 `mcp` SDK 包无命名冲突。
- `config/env.py`：`PROJECT_ROOT = parents[2]`；MCP 连接地址由 `demomcp.config.settings.tushare_mcp_url`（`TUSHARE_MCP_URL`）给出，这里不构造 stdio 参数（`build_stdio_params` 已移除）。
- **`create_all` 不会 ALTER 既有表**：改了 `models.py` 后要删掉旧的 `demo.db`（schema 漂移会 500）。
- **SSE 用 POST + `ReadableStream`** 解析，不是 `EventSource`（无法 POST body）。前端 `marked`+`DOMPurify` 先 sanitize 再渲染（LLM 输出不可信）。
- **错误永不崩图**：图内节点对 `llm.chat` 包 `except Exception` 转 `fallback`；`Agent.run` 再 `except Exception`（含 `ExceptionGroup`）转 `stopped_reason="error"`。二者都**不**捕 `BaseException`（`asyncio.CancelledError`，客户端断连/`task.cancel()` 照常透传）→ 避免 LangGraph 把节点异常汇成 `ExceptionGroup: unhandled errors in a TaskGroup` 上抛。
- 代理工具返回 `{code,msg,row_count,data}`；`code!=0`（如「无权限需提升积分」）是**非错误**结果，LLM 读友好 `msg` 自行调整；**只对抛出的异常**重试，`isError` 业务结果不重试。
- `Agent.run` 经图把 user/assistant/tool 各轮追加进 `messages`（最终答复也在历史里），多轮上下文靠它；Graph `messages` 是 OpenAI 风格 dict 列表、**普通 list 追加**（不用 `add_messages` reducer）。
- 前端会流式重渲染 Markdown（每帧 `DOMPurify.sanitize(marked.parse(累计文本))`），别在回调里做重活。
- **`demomcp/rag/__init__.py` 故意无 import**：重依赖（`pymupdf`/`faiss`/`torch`/`sentence_transformers`）在函数体内懒加载，`tests/test_rag_import_guards.py` 守住 import `demomcp.rag` 不触发下载/加载。
- **RAG 存储全为内存、不持久化**：`rag_vector_store_path`/`rag_hybrid_dense_weight` 是预留、未生效；`RagIndex`/`BM25`/`VectorStore` 每进程重建，无 SQLite/JSON 持久化（与 `demomcp/db/` 会话库无关）。
- **`rag_rerank_threshold` 代码默认 0.3**，`*.env.example` 与 `RAG_FINANCE.md` 写作 0.5，以代码为准（`settings.py` 注释：0.5 对真实 MD&A 文本过严）。`rag_embedding_model` 名为 `BAAI/bge-m3`，但无 `RAG_EMBEDDING_API_KEY`/`rag_use_real` 时回退 `HashingEmbedder`。
- **语义工具层**：`StockToolProvider` 的 `call_tool` 区分 `StockInputError`/`StockBusinessError`（→`is_error=False`，LLM 可自纠）与 `StockFetchError`（→`is_error=True`）；只对抛出的异常重试，业务结果不重试（与 MCP 层一致）。

## 关键文件

`demomcp/graph/{builder,nodes,routes,prompts,state}.py`（LangGraph 四节点）、`demomcp/agents/agent.py`（薄壳：构图 + ainvoke + 归一化 AgentResult）、`demomcp/providers/llm/deepseek.py`（流式/工具/回传 reasoning_content）、`demomcp/providers/tools/mcp.py`（HTTP 连接 + 自动发现/打印工具清单 + 重试/超时 + 权限失败友好提示）与 `providers/tools/stocks.py`（语义工具层：硬 allowlist + 4 语义工具 + 归一化）、`demomcp/rag/`（核心 `ingest` / `hybrid_retriever` / `citing` / `schemas`；`retriever.py` 为空实现占位）、`scripts/eval_rag.py`（离线评估 + 集成门禁）、`demomcp/entry/web.py`（SSE + sessions API + lifespan store）、`demomcp/db/{models,store}.py`、`demomcp/config/{settings,env}.py`（settings 含 `stock_*`/`rag_*`）、`mcp_server/server.py`（后备，默认停用）.
