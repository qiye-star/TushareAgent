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
agents      Agent 薄壳 = LangGraph 五节点编排（router/rewrite_query/tool_rag/synthesizer/fallback，只依赖 interfaces）
providers   tools: mcp/fake（+ stocks 语义层）；llm: deepseek/mock —— 可插拔适配器
rag         财报知识库（解析/混合检索/引用/持久化），经 Agent._get_retriever 注入实时图、离线优先
config      Settings + PROJECT_ROOT（MCP URL / DISCLAIMER / RAG rag_*）
db          SQLAlchemy 2.0 异步：ChatMessage（可读日志）+ ChatTurn（精确恢复）
entry       cli.py / web.py（SSE 流式 + /api/sessions）
mcp_server  内置数据 server（默认停用，仅后备；自建 streamable-http + 每接口一个工具，供本地/离线代理用）
```

**核心链路**：`Agent.run(history, on_text/on_thinking/on_tool/on_process)` → LangGraph 五节点 `router`（意图分类 + 越界判断）→ `rewrite_query`（RAG 查询改写）→ `tool_rag`（LLM 选工具 + **并行** `ToolProvider.call_tool` 与 RAG 检索——`RetrievalPlan` 检索进证据；`market` 意图跳过 RAG）→ `synthesizer`（整合 + 引用 + 免责声明）／越界或无证据（工具与 rag_chunks 双空）走 `fallback`。`tool_rag` 经**条件自环**支持**多轮取数**（agentic tool loop，`routes.make_route_after_tool_rag(max_iterations)`）：每轮执行后 LLM 看返回判定数据是否足够——足够就停止（返回无 `tool_calls`）去 `synthesizer`，仍缺就续调下一批工具；上限 `DEMO_MAX_ITERATIONS`（默认 10），`max_iterations=1` 即等价旧单轮行为。RAG **每轮只第一次检索**（`GraphState.rag_retrieved`）；`evidence/rag_chunks/tool_results` **跨轮累积**（LangGraph 以节点返回值整体替换这些键，`tool_rag` 必须读旧并回传全量）。**进展守卫**：某工具轮未新增任何证据/检索 → `no_progress_count` 递增，连续达 `no_progress_cap`（默认 2）即终止——防「无权限/空数据/查不到」时反复空转烧满 `max_iterations` 造成死循环（`agentic tool loop` 的已知坑）。循环可视化：节点每轮经 `on_process` 抛 `loop_turn{round,status,tools,evidence}`（status ∈ continue/stop/max-reached/no-progress），并给 `stage/plan/aggregate` 带 `round`，前端据此按轮渲染。节点内 `LLMClient.chat(stream…)`（DeepSeek 流式 + tool_calls + reasoning_content）透传回调；工具直接经 `MCPToolProvider.call_tool`（重试+超时）→ 官方 MCP（`TUSHARE_MCP_URL`），可查任意 A 股/任意接口。**选工具那轮 `llm.chat(tools=…)` 只喂相关子集**（`graph.tool_select.select_tools` per-query：恒保留 `list_apis/get_api_info/query/stock_basic`，其余按金融词汇重叠打分取 top-K（`TOOL_MAX_REVEALED`），可用性 `catalog` 剔除 blocked/down；执行仍走全量 `tools.call_tool`，不受限）。结果归一化为 `AgentResult`；错误一律转 `is_error` ToolResult，图永不崩。

**理由关键**：`Agent` 只依赖 `LLMClient` + `ToolProvider` 两协议；图在 `demomcp/graph` 内、由 `build_research_graph(llm, tools, tool_defs, …)` 闭包注入，节点经 `config.configurable` 读回调。消息帧（`assistant_message` / `tool_results_messages`）由 LLM 实现渲染 → 换后端不改图。`on_text`/`on_thinking` 在节点内流式吐出，`on_tool` 在 tool_rag 内逐次抛出（供前端做「思考轨迹」）。

**RAG（`demomcp/rag/`）**：针对 A 股年报 PDF 的问答型 RAG，**离线优先、独立于主链路**（不进 `entry→agents→interfaces`）。摄取：`pdf_parser`（PyMuPDF 解析 → 文本/图片/表格块）→ `section_tree`（字号聚类+编号正则检测章节、去目录页）→ `segments`（块归类、图片交 `captioner`、表格 `table_split` 行分块）→ `chunking`（句感知稠密分块 + 表格独立成块）→ `embedder`（`HashingEmbedder` 默认 / `ApiEmbedder` 走 OpenAI 兼容 `/embeddings`）+ `store`（默认 `InMemoryVectorStore`）& `bm25`（jieba 词元）。检索：`query_build` → `hybrid_retriever`（**三路 RRF**：节稠密+节 BM25+块稠密 → 按 `(doc,section)` 聚类 → top 节 → 重排池 → `reranker` → 阈值截断+`top_k`）→ `RagChunk`。引用：`citing` 确定性生成内联标记与参考列表（不编页码）。默认 `rag_use_real=False` → 纯 Python/确定性；真实后端需 `uv sync --extra rag-full` + `RAG_USE_REAL=true` + `RAG_EMBEDDING_API_KEY`。**已接入实时图**：`rag/runtime.build_runtime_retriever(config)` 懒加载 `hybrid_retriever.build_retriever`，`tool_rag` 按 `RetrievalPlan`（rewritten_query/concepts/filters/strategy，意图→strategy：report=factual/compare=structural）检索+重排进证据；`RAG_CORPUS_DIR` 可选启动时 ingest 建索引（无则检索为空、不崩）。`AgentResult.structured` 输出结构化 answer/claims/citations（`CiteRef` 元数据：公司/年份/页码/章节/内联）/metadata（`request` 归一化参数 + `validation.errors`）。`retriever.py` 的 `NullRetriever` 为遗留占位、无引用。文档：`docs/ARCHITECTURE.md`（现状系统架构含图）、`docs/RAG_INTEGRATION.md`（RAG 现状权威含全量配置表）、`docs/RAG_FINANCE.md`（设计蓝图）。取数面（曾为语义工具层 `StockToolProvider`，`stocks.py`）：已移除，现直接用原始 `MCPToolProvider`（`list_tools` 自动发现全部接口 + `query`），不再限定标的；`code!=0`（权限/积分不足）为业务结果 → 友好 `is_error=False` 让 LLM 转述，真异常才 `is_error=True`。

**Web**：`POST /chat` 走 SSE（`thinking/text/tool_call/tool_result/process/done/error`（+末尾 `__end__` 哨兵），`process.kind` ∈ intent/rewrite/stage/retrieval/funnel/plan/params/validation/aggregate，`tool_call{name,input}` → `tool_result{content,ok}`，前端据此渲染默认展开的工具卡），`ChatRequest` 支持可选 `model`（前端「深度思考」开关传 `deepseek-reasoner`，从而真正流出 `thinking`/reasoning_content）；`lifespan` 复用 `store`；`GET|DELETE /api/sessions`、`GET /api/sessions/{id}`；**恢复**用 `ChatTurn.messages_json`（每轮完整 OpenAI 消息，累计式）精确还原 tool_calls/tool_call_id；每轮 UI payload（answer/sources/citations/claims/metadata 等）落 `ChatTurnData`，经 `GET /api/sessions/{id}/turns` 回传。另挂 `POST /api/rag/retrieve` 与 `GET /api/rag/health`（`rag/server.py` 纯函数，lazy 构建 retriever，异常→degraded）。

## 关键坑（改了会踩）

- **`mcp` 钉 `>=1.28,<2`**（FastMCP）；`ClientSession.call_tool` 的 `read_timeout_seconds` 参数是 **`timedelta`，不是秒**（传 `timedelta(seconds=…)`）。
- **默认直连 Tushare 官方 MCP**：`tushare_mcp_url`（`TUSHARE_MCP_URL`）指向 `https://api.tushare.pro/mcp/?token=...`（token 放 URL query），`MCPToolProvider` 经 `streamablehttp_client(url)` 连接，工具由服务器暴露、`list_tools()` 自动发现并打印清单。内置 `mcp_server/` 默认停用（自建时会 `mcp.run(transport="streamable-http")`、读 `MCP_SERVER_HOST/PORT` 与 `TUSHARE_PROXY_*`），demo-mcp **不 import** 它 → 与 `mcp` SDK 包无命名冲突。
- `config/env.py`：`PROJECT_ROOT = parents[2]`；MCP 连接地址由 `demomcp.config.settings.tushare_mcp_url`（`TUSHARE_MCP_URL`）给出，这里不构造 stdio 参数（`build_stdio_params` 已移除）。
- **`create_all` 不会 ALTER 既有表**：改了 `models.py` 后要删掉旧的 `demo.db`（schema 漂移会 500）。
- **SSE 用 POST + `ReadableStream`** 解析，不是 `EventSource`（无法 POST body）。前端 `react-markdown`+`remark-gfm`+`remark-breaks` 渲染（默认不解析原始 HTML，天然防 XSS，无需 DOMPurify）。
- **错误永不崩图**：图内节点对 `llm.chat` 包 `except Exception` 转 `fallback`；`Agent.run` 再 `except Exception`（含 `ExceptionGroup`）转 `stopped_reason="error"`。二者都**不**捕 `BaseException`（`asyncio.CancelledError`，客户端断连/`task.cancel()` 照常透传）→ 避免 LangGraph 把节点异常汇成 `ExceptionGroup: unhandled errors in a TaskGroup` 上抛。
- 代理工具返回 `{code,msg,row_count,data}`；`code!=0`（如「无权限需提升积分」）是**非错误**结果，LLM 读友好 `msg` 自行调整；**只对抛出的异常**重试，`isError` 业务结果不重试。
- `Agent.run` 经图把 user/assistant/tool 各轮追加进 `messages`（最终答复也在历史里），多轮上下文靠它；Graph `messages` 是 OpenAI 风格 dict 列表、**普通 list 追加**（不用 `add_messages` reducer）。
- 前端会流式重渲染 Markdown（对累计文本每帧 `react-markdown` 解析），别在回调里做重活。
- **`demomcp/rag/__init__.py` 故意无 import**：重依赖（`pymupdf`/`pymilvus`/`milvus-lite`）在函数体内懒加载，`tests/test_rag_import_guards.py` 守住 import `demomcp.rag` 不触发下载/加载（嵌入/重排走服务端 API，本地无 torch/faiss/sentence-transformers）。
- **RAG 索引持久化在 `data/vectorstore/`（Milvus-Lite + SQLite `rag_rel.db`），且 Milvus-Lite 是单进程独占锁**：
  `rag_use_real=true` + 有 `has_index` → `runtime.load_index` 快载；索引**只有持有锁的进程能开**（web 当前持锁）。
  **其它进程**（CLI/脚本/测试/2nd worker）若也 `build_runtime_retriever` → 打不开 Milvus → 被 `Agent._get_retriever`
  吞成 `retriever=None`（静默无检索）。避免之道：**设 `RAG_HTTP_URL` 指向运行中的 web `/api/rag/retrieve`**，
  用 HTTP 取数而不打开 Milvus（`demomcp/rag/http_retriever.HttpRetriever`）；web 自身 `/chat` 仍用 in-process retriever。
  `rag_hybrid_dense_weight` 是预留、未接入 RRF 流。`rag_vector_store_path` 已生效（非预留）。
- **RAG 三路可被 strategy 收窄**：`factual`→仅 chunk 路、`structural`→仅节级两路、`auto`→全量（`hybrid_retriever`）；
  rerank API 失败 → 回退 RRF 序（`rerank_degraded` 计数）；`market` 意图在 `tool_rag` 里跳过 RAG（`_should_rag`）。
- **`rag_rerank_threshold` 代码默认 0.2**，`.env.example` 已对齐为 0.2；`RAG_FINANCE.md` 已按 0.2 更新（以代码为准）。`rag_candidate_k`/`rag_top_k_sections`/`rag_rerank_candidates` 走 recall-first（50/5/30）。`rag_embedding_model` 名为 `BAAI/bge-m3`，但无 `RAG_EMBEDDING_API_KEY`/`rag_use_real` 时回退 `HashingEmbedder`。
- **取数面全开**：不再有语义/白名单层；直接用 `MCPToolProvider`。`code!=0` 业务结果不重试、转友好 `is_error=False`（LLM 可自纠），真异常重试后 `is_error=True`（与 MCP 层一致）；权限/积分不足 → `is_error=False` 友好提示。
- **RAG 只在解析出语料公司时检索**：`_rag_retrieve` 在 `infer_filters(q).company is None`（非比亚迪/宁德时代/泛行业）时**直接空返**，避免无公司过滤时把别家年报切片搜出来污染答案（工具路兜底）。口语别名表下沉到 `rag/query_build.DOMAIN_ALIASES`（`nodes._domain_expand` 与 `tool_select._domain_terms` 共用，避免漂移）。
- **skill 所需接口恒保留**：`Skill.tool_families`（接口名子串）经 `curate(... skill_tools=...)` 传给 `select_tools`，这些接口**不进 `TOOL_MAX_REVEALED` 限额**（如 `ai_supply_chain_tracker` 保留 daily/daily_basic/forecast/announcement/moneyflow/top_list/…）。
- **可选万得（Wind）第二源**：`WIND_API_KEY`（Bearer 头）+ `WIND_ENABLED` 装配时，`entry/web|cli` 经 `providers/tools/wind.agent_tool_provider(settings)` 产出 `CompositeToolProvider([tushare, wind])`，万得 7 域工具统一加 `wind_` 前缀（`wind_get_stock_quote` 等，与 Tushare 原生名区分）；函数可容错，单域连接失败跳过、装配失败回退仅 Tushare。万得返回契约无 Tushare 的 `{code,msg,...}`，无 `code` 字段即视为成功。`.env` 未配 key / `WIND_ENABLED=false` → `settings.wind_configured=False` → 走原 Tushare 单源（行为不变）。

## 关键文件

`demomcp/graph/{builder,nodes,routes,prompts,state,tool_select}.py`（LangGraph 五节点 + 动态工具目录 `select_tools`）、`demomcp/agents/agent.py`（薄壳：构图 + ainvoke + 归一化 AgentResult）、`demomcp/providers/llm/deepseek.py`（流式/工具/回传 reasoning_content）、`demomcp/providers/tools/mcp.py`（HTTP 连接 + 自动发现/打印工具清单 + 重试/超时 + 权限失败友好提示）、`demomcp/providers/tools/wind.py`（万得 WindToolProvider + `agent_tool_provider` 装配）、`demomcp/providers/tools/composite.py`（多源合并 CompositeToolProvider）、`demomcp/providers/tools/curate.py`（可用性探测 `probe_availability`，默认关）、`demomcp/rag/`（核心 `runtime` / `persist` / `hybrid_retriever` / `citing` / `schemas` + `http_retriever`/`server`；`retriever.py` 为无引用遗留占位）、`scripts/{eval_rag,validate_rag,dba_rag}.py`（离线评估门禁/真引擎验证/建索引）、`demomcp/entry/web.py`（SSE + sessions API + RAG HTTP 端点 + lifespan store）、`demomcp/db/{models,store}.py`（ChatMessage/ChatTurn/ChatTurnData 三表）、`demomcp/config/{settings,env}.py`（settings 含 `rag_*` 与 `wind_*`）、`mcp_server/server.py`（后备，默认停用）.
