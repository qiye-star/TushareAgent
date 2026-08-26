# demo-mcp：可扩展的 LLM+MCP 数据对话助手

demo-mcp 是一个 **LLM + MCP** 数据对话助手：你用自然语言提问，内置的智能体会自己决定调用哪个工具、从 Tushare 数据代理取数，再总结成中文回答。支持 **CLI 终端** 与 **Web 控制台**（流式输出、思考轨迹、工具卡、Markdown、会话日志与恢复）。编排基于 **LangGraph 四节点状态机**，并附一个**独立的 RAG 财报知识库**（离线优先，暂未接入对话循环）。

- **LLM 后端 DeepSeek**（OpenAI 兼容端点，可换）；工具调度、错误处理都在本地。
- **自带 uv 环境**：`uv sync` 生成项目自己的 `.venv`，独立运行、互不干扰。
- **LangGraph 四节点编排**：`router`（意图分类 + 越界判断）→ `tool_rag`（选工具 + 执行）→ `synthesizer`（整合 + 引用 + 免责声明）／越界或无证据走 `fallback`。
- **语义工具层**：`StockToolProvider` 包裹 `MCPToolProvider`，硬允许列表**只放行「比亚迪 002594.SZ / 宁德时代 300750.SZ」**，对外暴露 4 个语义工具（`stock_available` / `stock_realtime_quote` / `stock_price_range` / `stock_financials`），隐藏通用 MCP `query`，并内置日期/复权口径/期数归一化。
- **连接 Tushare 官方 MCP**：应用经 `TUSHARE_MCP_URL` 直连 `https://api.tushare.pro/mcp/?token=...`，工具由官方服务器暴露、运行时自动发现（`list_tools`）并打印清单。内置 `mcp_server/`（本地代理→每接口工具）默认停用，仅作后备。
- **独立 RAG 财报知识库**（`demomcp/rag/`）：把 A 股年报 PDF（如比亚迪/宁德时代）解析 → 章节树 → 分块 → 混合检索（BM25 + 稠密三路 RRF 融合）→ 重排 → **确定性引用**（不靠 LLM 编页码），可离线评估（`scripts/eval_rag.py`）。
- **可 Docker 化**：`docker compose up` 一键起 Web 控制台。
- **SQLAlchemy 数据层**：每次会话的用户/助手/工具消息与完整对话上下文落到自己的库（`DEMO_DATABASE_URL`，默认 SQLite `demo.db`）。

> ⚠️ **RAG 尚未接入实时 agent 循环**：`demomcp/rag/` 及其评估/测试已完整，但 `retriever.py` 仍是空实现占位（`NullRetriever`），`GraphState.rag_chunks` 也未被填充——目前只有 `scripts/eval_rag.py` 与离线门禁使用它。完整设计蓝图见 `docs/RAG_FINANCE.md`、`docs/ARCHITECTURE.md`（其中部分属前瞻设计，与实测代码有出入）。

## 架构与层

```
demo-mcp/
├── pyproject.toml          依赖（运行时 + dev 组 + 可选 rag-full）+ uv 镜像
├── pyrightconfig.json      类型检查配置
├── .env / .env.example     项目配置
├── Dockerfile / docker-compose.yml   容器化部署
├── mcp_server/             内置 MCP 数据服务器（把 Tushare 数据代理暴露成 tools；默认停用）
├── docs/                   设计蓝图（ARCHITECTURE / RAG_FINANCE / tool-call-layer）
├── demomcp/
│   ├── interfaces/   契约层：类型 + ToolProvider / LLMClient 两协议（纯契约）
│   ├── agents/       薄壳：Agent（构图 + ainvoke + 归一化 AgentResult）+ CompositeToolProvider
│   ├── graph/        LangGraph 四节点（router / tool_rag / synthesizer / fallback）
│   ├── providers/    实现层：可插拔适配器（tools: mcp/fake + stocks 语义层；llm: deepseek/mock）
│   ├── rag/          RAG 财报知识库（PDF 摄取 / 混合检索 / 确定性引用；离线优先）
│   ├── db/           数据层：SQLAlchemy 2.0 异步，会话历史存取（独立库）
│   ├── config/       配置层：Settings + 项目根 + MCP URL + 语义工具/RAG 配置
│   └── entry/        入口层：CLI / Web（SSE 流式）
├── tests/            离线 gate（FakeToolProvider + MockLLM + 内存 SQLite；RAG/语义工具单测）
├── scripts/          scripts/smoke_e2e.py（真实端到端） + scripts/eval_rag.py（RAG 离线评估）
└── web/              前端（SSE + Markdown + 侧栏历史会话 + 工具卡 / 思考面板）
```

依赖方向自上而下：`entry → agents → interfaces`；`providers → interfaces`；`config` 为叶子；`db` 被 entry 使用；`rag` **不参与**主链路（独立、可单独调用/评估）。

## 前置要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 一个 **Tushare 官方 MCP 地址**（`https://api.tushare.pro/mcp/?token=...`，token 放 URL query）。把它填到 .env 的 `TUSHARE_MCP_URL` 即可（应用直连官方 MCP，无需自建数据代理）。
- （可选）RAG 真实后端：`uv sync --extra rag-full` 安装 `pymupdf`/`jieba`/`rank-bm25`。

## 快速开始（本地）

```bash
cd demo-mcp
uv sync                                    # 生成 .venv + uv.lock（首次联网装依赖）
cp .env.example .env                       # 填 DS_API_KEY / TUSHARE_MCP_URL（含 token）；RAG 相关可选
```

在 `.env` 里设 `TUSHARE_MCP_URL`（Tushare 官方 MCP 地址，含 token）后，直接运行应用：

```bash
# CLI（终端对话）
.venv/Scripts/python.exe -m demomcp.entry.cli        # Windows
# .venv/bin/python -m demomcp.entry.cli              # Linux / macOS

# Web 控制台（SSE 流式；打开 http://127.0.0.1:8010）
.venv/Scripts/python.exe -m uvicorn demomcp.entry.web:app --port 8010
```

示例会话：`比亚迪最近一个月的日线` / `宁德时代近两年的研发投入` —— 助手直接调用对应语义工具取数，最后总结输出，并把每轮消息与完整上下文落到 `demo.db`（可用侧栏查看/恢复）。语义工具层只放行比亚迪/宁德时代两只标的。

### RAG 评估（可选，独立于对话循环）

RAG 默认离线/确定性（纯 Python 哈希嵌入 + 内存向量库 + 自包含 BM25，不联网、不装 `rag-full`、不需要 API key）；要真嵌入/重排才装 `rag-full` 并设 `RAG_USE_REAL=true` + `RAG_EMBEDDING_API_KEY`。

```bash
uv sync --extra rag-full                                   #（可选）装 RAG 重依赖
.venv/Scripts/python.exe scripts/eval_rag.py               # 离线评估（默认合成语料）
.venv/Scripts/python.exe scripts/eval_rag.py \
    --corpus "docs/比亚迪：2025年年度报告.pdf"                # 对真实 PDF 评估（需 rag-full）
```

评估用 `tests/rag_golden/qa.yaml`（或 `--gold` 指定），输出 JSON 指标，并通过门禁：`context_recall@k ≥ 0.8 && faithfulness ≥ 0.7 && cross_company_isolation == 1.0`（退出码 0/1）。

## Docker 运行

```bash
cd demo-mcp
cp .env.example .env        # 填 DS_API_KEY / TUSHARE_MCP_URL（含 token）等
docker compose up           # 构建镜像并启动 Web，打开 http://localhost:8010
```

或手动：

```bash
docker build -t demo-mcp .
docker run --rm -p 8010:8010 --env-file .env demo-mcp
```

> `docker compose up` 启动 Web；`demo` 经 `.env` 的 `TUSHARE_MCP_URL` 连接 Tushare 官方 MCP。会话历史库通过卷 `demo_data` 持久化到 `/app/data/demo.db`。

## 配置（.env）

### LLM / Agent / MCP / 会话库

| 变量 | 默认 | 说明 |
|---|---|---|
| `DS_API_KEY` | — | DeepSeek API key（必填） |
| `DS_BASE_URL` | `https://api.deepseek.com` | DeepSeek 端点（网关/自定义部署时改） |
| `DS_MODEL` | `deepseek-chat` | 模型 id（推理类可换能出 `reasoning_content` 的） |
| `DS_STREAMING` | `true` | 是否流式 |
| `DS_MAX_TOKENS` | `8192` | 单次回复最大 token |
| `DEMO_SYSTEM_PROMPT` | 内置双票金融 prompt | 系统提示词 |
| `DEMO_MAX_ITERATIONS` | `10` | 一次提问内 agent 最大循环轮数 |
| `DISCLAIMER` | 内置免责声明 | 合成器/兜底文案追加的免责声明 |
| `DEMO_DATABASE_URL` | 空→`demo.db` | 会话历史库；也支持 `mysql/asyncmy`、`postgres/asyncpg` |
| `TUSHARE_MCP_URL` | `https://api.tushare.pro/mcp/` | Tushare 官方 MCP 地址（token 放 URL query）；应用经它连接，工具运行时自动发现 |
| `DEMO_MCP_TIMEOUT` | `30` | 单次 MCP 工具调用读超时（秒） |
| `DEMO_MCP_RETRIES` | `2` | 工具调用重试次数（异常与业务失败都会重试） |

### 语义工具层（双票限定）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEMO_STOCKS` | `002594.SZ,300750.SZ` | 硬允许列表（逗号分隔 ts_code）；**仅比亚迪/宁德时代**，其它标的会被拒绝 |
| `STOCK_DEFAULT_ADJ` | `qfq` | 区间涨跌幅默认复权口径（`qfq`/`hfq`/`none`） |
| `STOCK_FINANCIAL_PERIODS` | `8` | 财务数据默认期数（近 2 年 = 8 期） |

### RAG 财报知识库（离线优先）

默认全为「纯 Python / 确定性」：`RAG_USE_REAL=false` 时用哈希嵌入 + 内存向量库 + 自包含 BM25，不联网、不需要 `rag-full`。要真嵌入/重排，才设 `RAG_USE_REAL=true` 并装 `rag-full`、填 `RAG_EMBEDDING_API_KEY`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAG_USE_REAL` | `false` | 走真实后端（bge-m3 嵌入 / faiss / pymupdf）；false → 纯 Python 兜底 |
| `RAG_VECTOR_STORE_PATH` | `<root>/data/vectorstore` | ⚠️ 预留：当前代码**不持久化**，向量库全为内存，进程重建 |
| `RAG_EMBEDDING_MODEL` | `BAAI/bge-m3` | 嵌入模型（OpenAI 兼容 `/embeddings` 服务端）；`hashing`=测试兜底 |
| `RAG_EMBEDDING_API_BASE` | `https://api.siliconflow.cn/v1` | 嵌入服务 base_url |
| `RAG_EMBEDDING_API_KEY` | `""` | 嵌入服务 api_key；为空则回退 hashing |
| `RAG_EMBEDDING_DIM` | `256` | hashing 嵌入维度（bge-m3 维度由响应自动发现） |
| `RAG_CAPTIONER` | `deepseek-v4-flash-vision-exp` | 图表多模态描述模型名；无 `DS_API_KEY` 回退 `Noop`（跳过图片） |
| `RAG_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | 重排模型（SiliconFlow 服务端）；无 key 回退 `Noop` |
| `RAG_TOP_K` | `5` | 返回给下游的 top-k |
| `RAG_CANDIDATE_K` | `30` | 每路候选数 |
| `RAG_TOP_K_SECTIONS` | `3` | 聚合时保留的 top 章节数 |
| `RAG_SCORE_THRESHOLD` | `0.3` | 粗筛分数阈值 |
| `RAG_RERANK_THRESHOLD` | `0.3` | ⚠️ 重排后阈值。**代码默认 0.3**；历史文档 / `*.env.example` 记为 `0.5`，**以代码为准** |
| `RAG_RERANK_CANDIDATES` | `24` | 重排候选池容量（每 top 章节 per_section 个） |
| `RAG_FIN_DENSE_CHUNK` | `400` | 稠密块目标字符数 |
| `RAG_FIN_DENSE_OVERLAP` | `80` | 稠密块重叠字符数 |
| `RAG_SECTION_MAX_CHARS` | `8000` | 整节文本上限（供 BM25） |
| `RAG_TABLE_ROWS_PER_CHUNK` | `8` | 表格行分块行数 |
| `RAG_HYBRID_DENSE_WEIGHT` | `0.6` | ⚠️ 预留（当前走 RRF 融合） |
| `RAG_STRICT_SCOPE` | `true` | 严格限定语料来源 |
| `RAG_HYDE` | `false` | 查询扩展 / hyDE |
| `RAG_STRATEGY` | `auto` | `structural` / `factual` / `auto` |
| `RAG_TABLE_ENGINE` | `pymupdf` | 表格抽取引擎：`pymupdf` / `camelot` |
| `RAG_RRF_K` | `60` | RRF 的 k |
| `RAG_BM25_K1` | `1.5` | BM25 k1 |
| `RAG_BM25_B` | `0.75` | BM25 b |

> 内置 `mcp_server/`（本地代理→每接口工具）默认停用，仅作后备；单独跑它时才需 `MCP_SERVER_HOST/PORT` 与 `TUSHARE_PROXY_*`。

> 配置只读 `demo-mcp/.env`（pydantic-settings）。不要提交 `.env`（已被 `.gitignore` 忽略）。

## Web 端功能（`/chat` 为 SSE 流式）

- **流式输出**：`thinking`（模型 reasoning_content，若有）/ `text`（内容增量）/ `tool_call{name,input}` / `tool_result{content,ok}` / `done` / `error` 事件。
- **思考过程**：`reasoning_content` 在**默认展开的「思考过程」面板**里逐字流式呈现（顶栏「深度思考」开关会请求 `deepseek-reasoner`，从而真正产生 thinking；注意其工具调用支持取决于 API 版本）。
- **工具调用**：`tool_call` + `tool_result` 渲染为**默认展开的工具卡**（工具名 + 入参 JSON + 返回数据自动表格化/键值化 + ✔成功/✖失败 chip）。语义工具层只暴露 4 个工具；RAG 类答案以普通 Markdown（含内联引用）返回。
- **Markdown**：助手/用户消息用 `marked` + `DOMPurify`（`web/vendor/` 本地优先，CDN 兜底，离线可用）渲染。
- **滚动**：自动跟随仅在接近底部时启用，用户上翻历史时不强制跳底。
- **日志/恢复**：侧栏列出历史会话（`GET /api/sessions`），点开看消息（`GET /api/sessions/{id}`）并可**继续**（服务端用完整上下文恢复）；也可删除（`DELETE /api/sessions/{id}`）。出错时工具 `is_error` 与 agent 异常以 `role=tool/error` 落库，侧栏可定位。

## RAG 财报知识库（`demomcp/rag/`）

针对 A 股年报 PDF 的**问答型** RAG，离线优先、可评估。完整管线：

- **摄取（离线，逐文档）**：`pdf_parser`（解析 PDF → 文本/图片/表格块）→ `section_tree`（字号聚类 + 编号正则**检测章节**，去目录页）→ `segments`（块归类、图片交给 captioner、表格行分块）→ `chunking`（句感知稠密分块 + 表格独立成块）→ `embedder` + `store`（向量化并入内存库）& `bm25`（jieba 词元、按节做词法索引）。
- **检索（在线）**：`query_build`（改写查询 + 金融词扩展 + 提取概念）→ `hybrid_retriever`（**三路 RRF 融合**：节稠密 + 节 BM25 + 块稠密 → 按 `(doc, section)` 聚合并取 top 节 → 每节 top-k 块进入重排池）→ `reranker`（服务端模型，无 key 则 `Noop`）→ 阈值截断 + `top_k` → 带原文来源的 `RagChunk`。
- **确定性引用**：`citing` 把 `RagChunk` 转成内联标记 `[比亚迪2024年报 - 第42页 3.2 研发投入]` 与参考列表（**不靠 LLM 编页码**），页面号来自章节树/表格区块的页段。

**默认离线、100% 确定性**：`HashingEmbedder`（blake2b 特征哈希）+ `InMemoryVectorStore`（余弦）+ 自包含 BM25 + `NoopReranker` + `NoopCaptioner`——不联网、无模型权重、不需要 `rag-full`。`rag/__init__.py` 故意不 import，重依赖（`pymupdf`/`faiss`/`torch`/`sentence_transformers`）在函数体内懒加载，由 `tests/test_rag_import_guards.py` 守住。

> ⚠️ **尚未接入实时 agent 循环**：`demomcp/rag/retriever.py` 仍是空实现占位（`NullRetriever.retrieve` 恒返回 `[]`），`GraphState.rag_chunks` 是预留字段、**从未被填充**；图内 `tool_rag` 节点目前只做工具选择+执行，**不调用任何 retriever**。真实入口是 `hybrid_retriever.build_retriever(config)`（`RagRetriever.retrieve(plan)`），目前仅 `scripts/eval_rag.py` 与离线门禁使用。
> 注意 **接口签名不一致**：占位 `NullRetriever.retrieve(query: str)` 与真实 `RagRetriever.retrieve(plan: RetrievalPlan)` 不同，接入时需统一为 plan 形式。
>
> 更完整的设计蓝图（含未来的跨公司结构化合成、`sentence-transformers`/`chromadb`/`faiss` 等候选）见 `docs/RAG_FINANCE.md` 与 `docs/ARCHITECTURE.md`——其中部分属前瞻设想，**与当前实测代码有出入**；本 README 以代码实现为准。

## 离线测试（无需代理 / key / 网络）

```bash
cd demo-mcp
.venv/Scripts/python.exe -m pytest tests -q       # 或 .venv/bin/python
```

覆盖：

- **主链路**：agent 循环全链路（`FakeToolProvider` + `MockLLM`）、LangGraph 四节点路由（越界 → `fallback`）、`code!=0` 作为正常结果、工具异常转 `is_error`、迭代上限、MCP 重试/超时。
- **语义工具层**：`test_stock_input.py`（代码/日期/复权/期数归一化、硬允许列表）、`test_stock_provider.py`（`StockToolProvider` 语义工具 + 错误约定）。
- **RAG**：16 个 `test_rag_*.py`（PDF 解析、章节树、表格分块、BM25、向量库、重排、引用、摄取幂等、真实 PDF 端到端、sparse import 守卫、离线评估门禁）。
- **配置/数据层**：默认值/路径/DB URL；内存 SQLite 的 `ChatMessage` 日志 + `ChatTurn` 恢复往返。

## 真实端到端冒烟（可选）

需真 `DS_API_KEY` + `.env` 的 `TUSHARE_MCP_URL` 指向官方 MCP 且 token 有效：

```bash
.venv/Scripts/python.exe scripts/smoke_e2e.py
```

## 静态检查

```bash
cd demo-mcp
.venv/Scripts/ruff.exe check demomcp tests scripts
PYRIGHT_PYTHON_NODE_VERSION=22.23.2 .venv/Scripts/pyright
```

## 扩展：层 = 扩展点

| 层 | 职责 | 扩展方式 |
|---|---|---|
| `entry` | 启动方式（CLI / Web / 批量 / HTTP API） | 加一个入口文件 |
| `agents` | LangGraph 四节点编排、Agent 薄壳 | 改 `agent.py` / `graph/*` |
| `graph` | 意图路由 → 工具执行 → 综合/兜底 | 改 `nodes.py` 或加新节点 |
| `interfaces` | 类型 + 两协议 | 改协议即改所有适配器（谨慎） |
| `providers/tools` | 工具来源（MCP / 假 / 语义层 stocks） | 加 `xxx.py`；语义层改 `stocks.py` |
| `providers/llm` | 具体 LLM 后端 | 加 `xxx.py` 实现 `interfaces.llm_client` |
| `rag` | 财报知识库（解析/检索/引用） | 改 `rag/*`；接入图时替换 `retriever.py` 占位 |
| `db` | 会话历史持久化 | 加模型 / 扩展 `store.py` |
| `config` | 环境变量、路径、透传参数 | 加字段即可 |
