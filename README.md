# demo-mcp：可扩展的 LLM+MCP 数据对话助手

demo-mcp 是一个 **LLM + MCP** 数据对话助手：用自然语言提问，内置智能体自动决定调用哪个工具、从 **Tushare 官方 MCP** 取数，并**并行检索 RAG 财报知识库**，最后综合成带**确定性引用**的中文回答——支持 **CLI** 与 **Web 控制台**（流式输出、思考轨迹、工具卡、会话日志与恢复）。核心编排为 **LangGraph 状态机**（router → rewrite_query → tool_rag ——（条件自环）→ synthesizer / fallback，`tool_rag` 可**多轮取数**：LLM 每轮基于已见返回判定是否继续，数据足够或达上限才收尾）；RAG 财报知识库面向 A 股年报 PDF（离线优先、已对接实时循环、可评估）。

> ✅ **当前质量状态（2026-08-27 实测）**：pytest **169 项零失败**；RAG 离线门禁全达标（recall@k **1.0** / faithfulness **0.9** / 跨公司隔离 **1.0**）；真引擎 BYD/CATL recall **8/8**、双向隔离 ✅、EXIT 0；E2E 数值与官方 MCP 直查**逐位一致**；**总评分 89.4 / 100**（唯一余留：表格溯源 0/2）— 见 [TEST_REPORT.md](docs/TEST_REPORT.md)。

- **LLM 后端 DeepSeek**（OpenAI 兼容端点，可换）；工具调度、错误处理都在本地。
- **自带 uv 环境**：`uv sync` 生成项目自己的 `.venv`，独立运行、互不干扰。
- **LangGraph 编排**：`router`（意图分类 + 越界判断）→ `rewrite_query`（RAG 查询改写）→ `tool_rag`（**并行**：LLM 选工具 + RAG 检索）→ `synthesizer`（综合 + 引用 + 免责声明）／越界或无证据走 `fallback`。`tool_rag` 经**条件自环**支持**多轮取数**（agentic tool loop）：每轮执行后 LLM 看返回，仍缺数据就续调、判定足够就停止，最多 `DEMO_MAX_ITERATIONS`（默认 10）轮——一轮一轮是图上真实节点调用，每轮由前端 `process.loop_turn` 事件可视化。
- **全量 MCP 查询**：应用直接使用原始 `MCPToolProvider`（不叠加语义/白名单层），LLM 看到服务端暴露的工具（`list_apis` / `get_api_info` / `query` + 各接口工具如 `daily`/`income`/`stock_basic`），可查**任意 A 股上市公司/指数/任意接口**，不再限定比亚迪/宁德时代。为防上下文爆满，`tool_rag` 选工具那轮只把**相关子集**喂给 LLM（`graph/tool_select.select_tools`：meta 发现工具恒在 + 金融词汇打分取 top-K，`TOOL_MAX_REVEALED`），执行仍走全量 provider；可用性探测（`scripts/probe_tools.py`，默认关）可选剔除积分/下线接口。
- **连接 Tushare 官方 MCP**：应用经 `TUSHARE_MCP_URL` 直连 `https://api.tushare.pro/mcp/?token=...`，工具由官方服务器暴露、运行时自动发现（`list_tools`）并打印清单。内置 `mcp_server/`（本地代理→每接口工具）默认停用，仅作后备。
- **独立 RAG 财报知识库**（`demomcp/rag/`）：把 A 股年报 PDF（如比亚迪/宁德时代）解析 → 章节树 → 分块 → 混合检索（BM25 + 稠密**三路 RRF** 融合）→ 重排 → **确定性引用**（不靠 LLM 编页码）；索引持久化到 `data/vectorstore/`（**Milvus-Lite + SQLite**），可离线评估（`scripts/eval_rag.py`）。
- **可 Docker 化**：`docker compose up` 一键起 Web 控制台。
- **SQLAlchemy 数据层**：每次会话的用户/助手/工具消息、完整上下文（`ChatTurn`）与每轮 UI 数据（`ChatTurnData`，含结构化引用）落到自己的库（`DEMO_DATABASE_URL`，默认 SQLite `demo.db`）。

> 📖 全部架构、原理、运维细节见下方「文档（docs/）」一节；设计蓝图类文档与代码冲突时，**以代码与 `docs/ARCHITECTURE.md`、`docs/RAG_INTEGRATION.md` 为准**。

## 文档（docs/）

| 文档 | 一句话说明 | 适合读者 |
|---|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | **系统架构（当前实现）**：五节点 LangGraph 状态机与条件边、各层组件与运行语义、会话持久化三表、配置与部署形态；含分层/状态机/SSE 时序/ER 图等 5 张图 | 所有开发/维护者，从这读起 |
| [RAG_INTEGRATION.md](docs/RAG_INTEGRATION.md) | **RAG 集成与运行（当前实现）**：检索计划构造、三路 RRF、重排与阈值、证据组装、Milvus-Lite+SQLite 持久化、`/api/rag/retrieve` HTTP 服务、RAG_* 全量默认值表与三个验证脚本（`eval_rag`/`validate_rag`/`dba_rag`） | 修改/调试 RAG 的开发者 |
| [RAG_FINANCE.md](docs/RAG_FINANCE.md) | **RAG 设计蓝图**（含「实现状态（截至 2026-08-27）」核对表）：**为什么**混合检索、如何保证引用不编造、评估门禁标准；含未来项（hyDE、质量自检回环等） | 想改检索算法/评估的开发者 |
| [tool-call-layer.md](docs/tool-call-layer.md) | ~~语义工具层设计（双票限定）~~ **已废弃/绕过**：现直达原始 MCP 做全量查询，不再叠加语义层 | 历史背景/弃用说明 |
| [TEST_REPORT.md](docs/TEST_REPORT.md) | **测试报告（2026-08-27，3 次测试 + 量化评分卡）**：pytest 169 项全量、RAG 离线评估 5 项指标、真引擎验证（达标：BYD/CATL recall 1.00、隔离 ✅；余留：表格溯源 0/2）、真实端到端冒烟（数值与官方 MCP 直查一致）、**总评分 89.4/100** | 关注质量门禁的历史记录 |

> `docs/` 目录内另有 3 份年报 PDF（语料样本，**非文档**）。

## 架构与层

```
demo-mcp/
├── pyproject.toml          依赖（运行时 + dev 组 + 可选 rag-full）+ uv 镜像
├── pyrightconfig.json      类型检查配置
├── .env / .env.example     项目配置
├── Dockerfile / docker-compose.yml   容器化部署
├── mcp_server/             内置 MCP 数据服务器（把 Tushare 数据代理暴露成 tools；默认停用）
├── docs/                   文档：ARCHITECTURE（现状架构）/ RAG_INTEGRATION（RAG 现状）/ RAG_FINANCE（设计蓝图）/ tool-call-layer
├── demomcp/
│   ├── interfaces/   契约层：类型 + ToolProvider / LLMClient 两协议（纯契约）
│   ├── agents/       薄壳：Agent（构图 + ainvoke + 归一化 AgentResult）
│   ├── graph/        LangGraph 五节点（router / rewrite_query / tool_rag / synthesizer / fallback）
│   ├── providers/    实现层：可插拔适配器（tools: mcp/fake + stocks 语义层；llm: deepseek/mock）
│   ├── rag/          RAG 财报知识库（PDF 摄取 / 混合检索 / 持久化 / 确定性引用；离线优先）
│   ├── db/           数据层：SQLAlchemy 2.0 异步，会话历史 + 每轮 UI 数据（独立库）
│   ├── config/       配置层：Settings + 项目根 + MCP URL + RAG 配置
│   └── entry/        入口层：CLI / Web（SSE 流式 + REST + RAG HTTP 端点）
├── tests/            离线 gate（FakeToolProvider + MockLLM + 内存 SQLite；RAG/MCP 取数单测）
├── scripts/          smoke_e2e.py（真实端到端）/ eval_rag.py（RAG 离线评估）/ validate_rag.py（真引擎验证）/ dba_rag.py（离线建索引）
└── scripts/web/      React + TS + Tailwind 前端（Vite + Zustand；三栏工作台：会话 / 对话 / 配置面板）
```

依赖方向自上而下：`entry → agents → interfaces`；`providers → interfaces`；`config` 为叶子；`db` 被 entry 使用；`rag` 经 `Agent._get_retriever` **注入**图（另有独立脚本/评估入口）。

## 前置要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 一个 **Tushare 官方 MCP 地址**（`https://api.tushare.pro/mcp/?token=...`，token 放 URL query）。把它填到 .env 的 `TUSHARE_MCP_URL` 即可（应用直连官方 MCP，无需自建数据代理）。
- （可选）RAG 真实后端：`uv sync --extra rag-full` 安装 `pymilvus`/`milvus-lite`（向量库）/`pymupdf`（PDF 解析）/`jieba`/`rank-bm25`——嵌入与重排走**服务端 API**（SiliconFlow bge-m3 / bge-reranker-v2-m3），本地不上 torch/faiss/sentence-transformers。

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
# 前端为 React + TS + Tailwind（Vite 工程，见 scripts/web/）；先构建产物再由后端同源服务
cd scripts/web && npm ci && npm run build && cd ../..
.venv/Scripts/python.exe -m uvicorn demomcp.entry.web:app --port 8010
# 开发模式：另开终端 `cd scripts/web && npm run dev`（Vite 代理 /chat、/api 到 8010）
```

示例会话：`比亚迪最近一个月的日线` / `贵州茅台近两年的营收` —— 助手经 `list_apis`/`get_api_info` 确定接口后用 `query`/对应接口工具取数（并**并行检索**年报 RAG，语料仍为比亚迪/宁德时代年报），最后总结输出（含内联引用），并把每轮消息、完整上下文与结构化 UI 数据落到 `demo.db`（可用侧栏查看/恢复）。标的与接口不再受限，可查任意 A 股数据。

### RAG 索引与检索（可选）

RAG 默认离线/确定性（纯 Python 哈希嵌入 + 内存向量库 + 自包含 BM25，不联网、不装 `rag-full`、不需要 API key）；要真嵌入/重排/持久化索引，才装 `rag-full` 并设 `RAG_USE_REAL=true` + `RAG_EMBEDDING_API_KEY`，然后用 `dba_rag.py` 建索引：

```bash
uv sync --extra rag-full                                    #（可选）装 RAG 重依赖
# .env: RAG_USE_REAL=true + RAG_VECTOR_STORE_PATH + RAG_CORPUS_DIR + RAG_EMBEDDING_API_KEY
.venv/Scripts/python.exe scripts/dba_rag.py                        # 建索引（增量；--rebuild 全重建）
.venv/Scripts/python.exe scripts/eval_rag.py                       # 离线评估（默认合成语料）
.venv/Scripts/python.exe scripts/eval_rag.py \
    --corpus "docs/比亚迪：2025年年度报告.pdf" --gold tests/rag_golden/qa_real.yaml
.venv/Scripts/python.exe scripts/validate_rag.py                   # 真引擎对两份真实年报做验证门禁
```

- 索引落在 `data/vectorstore/`（`milvus.db` + `rag_rel.db`）；web 启动后按 `has_index` 自动快载。
- **多进程注意**：Milvus-Lite 单进程独占锁——web 持锁时，CLI/脚本/第二个 worker 设 `RAG_HTTP_URL=http://127.0.0.1:8010` 走进程内 HTTP 检索（`/api/rag/retrieve`），不要直接开本地索引。
- 评估门禁：`context_recall@k ≥ 0.8 && faithfulness ≥ 0.7 && cross_company_isolation == 1.0`（退出码 0/1）。

## Docker 运行

**前置**：Docker Desktop 已启动（`docker info` 能连上 engine）；`.env` 已填好 `DS_API_KEY` / `TUSHARE_MCP_URL`（含 token）；宿主端口 `8010` 未被占用。

```bash
cd demo-mcp
cp .env.example .env        # 填 DS_API_KEY / TUSHARE_MCP_URL（含 token）等必需项
docker compose up           # 一键：构建(两阶段) + 启动 Web，打开 http://localhost:8010
```

- **构建（两阶段）**：`node:20-alpine` 先出 `scripts/web/dist` 静态资源 → `python:3.11-slim` 装 `uv` 运行时，`uv run uvicorn demomcp.entry.web:app`（`0.0.0.0:8010`）。`web.py` 直接托管 `scripts/web/dist`。
- `.env` 经 `env_file` 注入；会话历史用 SQLite，写进卷 `demo_data`（容器内 `/app/data`），`demo.db` 与 RAG `data/vectorstore/` 同卷持久化。
- `.dockerignore` 已排除 `.env / .venv / scripts/web/dist / demo.db / *.db / docs`，构建上下文干净。

不走 compose、单独跑：

```bash
docker build -t demo-mcp .
# 用卷持久化历史库（/app/data），把宿主 8010 映射到容器 8010
docker run --rm -p 8010:8010 --env-file .env \
  -e DEMO_DATABASE_URL=sqlite+aiosqlite:////app/data/demo.db \
  -v demo_data:/app/data demo-mcp
```

> **端口冲突**：若宿主 `8010` 已被本地实例占用，换宿主端口——`docker run -p 8011:8010 ...`，或把 `docker-compose.yml` 的 `"8010:8010"` 改成如 `"8011:8010"`。
> **健康检查**：`curl http://localhost:8010/api/sessions`（返回 `[]` 表示已就绪）。
> **日志**：`docker compose logs -f demo`（应用 stdout）。对话文本日志在容器内写 `/app/logs/chat.log`（该目录不在卷里，重启即清；需要持久化可追加 `-v demo_logs:/app/logs`）。
> **停止/清理**：`docker compose down`；连同数据卷一起删加 `-v`。

## 配置（.env）

### LLM / Agent / MCP / 会话库

| 变量 | 默认 | 说明 |
|---|---|---|
| `DS_API_KEY` | — | DeepSeek API key（必填） |
| `DS_BASE_URL` | `https://api.deepseek.com` | DeepSeek 端点（网关/自定义部署时改） |
| `DS_MODEL` | `deepseek-chat` | 模型 id（推理类可换能出 `reasoning_content` 的） |
| `DS_STREAMING` | `true` | 是否流式 |
| `DS_MAX_TOKENS` | `8192` | 单次回复最大 token |
| `DEMO_SYSTEM_PROMPT` | 内置全量金融 prompt（任意 A 股标的/全量接口，指引 list_apis→get_api_info→query） | 系统提示词 |
| `DEMO_MAX_ITERATIONS` | `10` | agentic tool loop 取数轮次上限（`tool_rag` 条件自环；LLM 判定数据足够即提前结束） |
| `DISCLAIMER` | 内置免责声明 | 合成器/兜底文案追加的免责声明 |
| `DEMO_DATABASE_URL` | 空→`demo.db` | 会话历史库；也支持 `mysql/asyncmy`、`postgres/asyncpg` |
| `TUSHARE_MCP_URL` | `https://api.tushare.pro/mcp/` | Tushare 官方 MCP 地址（token 放 URL query）；应用经它连接，工具运行时自动发现 |
| `DEMO_MCP_TIMEOUT` | `30` | 单次 MCP 工具调用读超时（秒） |
| `DEMO_MCP_RETRIES` | `2` | 工具调用重试次数（**异常与业务失败都会重试**；权限/积分失败重试后转友好提示，非错误） |

### RAG 财报知识库（离线优先，已接入实时循环）

默认全为「纯 Python / 确定性」：`RAG_USE_REAL=false` 时用哈希嵌入 + 内存向量库 + 自包含 BM25，不联网、不需要 `rag-full`。要真嵌入/重排/持久化，才设 `RAG_USE_REAL=true` 并装 `rag-full`、填 `RAG_EMBEDDING_API_KEY`（嵌入/重排在 SiliconFlow 服务端跑模型）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `RAG_USE_REAL` | `false` | 走真实后端（bge-m3 服务端嵌入 / Milvus-Lite / pymupdf）；false → 纯 Python 兜底 |
| `RAG_VECTOR_STORE_PATH` | `<root>/data/vectorstore` | **索引落盘目录**：`milvus.db`（Milvus-Lite）+ `rag_rel.db`（SQLite 关系库）；`has_index` → 启动快载 |
| `RAG_CORPUS_DIR` | `""` | **显式设置才摄取**的 PDF 语料目录（空不自动扫描 docs\） |
| `RAG_EMBEDDING_MODEL` | `BAAI/bge-m3` | 嵌入模型（OpenAI 兼容 `/embeddings` 服务端）；`hashing`=测试兜底 |
| `RAG_EMBEDDING_API_BASE` | `https://api.siliconflow.cn/v1` | 嵌入服务 base_url |
| `RAG_EMBEDDING_API_KEY` | `""` | 嵌入服务 api_key；为空则回退 hashing |
| `RAG_EMBEDDING_DIM` | `256` | hashing 嵌入维度（bge-m3 维度由响应自动发现） |
| `RAG_CAPTIONER` | `deepseek-v4-flash-vision-exp` | 图表多模态描述模型名；无 `DS_API_KEY` 回退 `Noop`（跳过图片） |
| `RAG_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | 重排模型（SiliconFlow 服务端）；无 key 回退 `Noop` |
| `RAG_TOP_K` | `5` | 返回给下游的 top-k |
| `RAG_CANDIDATE_K` | `50` | 每路候选数（recall-first） |
| `RAG_TOP_K_SECTIONS` | `5` | 聚合时保留的 top 章节数 |
| `RAG_SCORE_THRESHOLD` | `0.3` | ⚠️ 已声明但代码**未读取**（预留） |
| `RAG_RERANK_THRESHOLD` | `0.2` | 重排后阈值（重排失败回退 RRF 序时跳过） |
| `RAG_RERANK_CANDIDATES` | `30` | 重排候选池硬上限 |
| `RAG_FIN_DENSE_CHUNK` | `400` | 稠密块目标字符数 |
| `RAG_FIN_DENSE_OVERLAP` | `80` | 稠密块重叠字符数 |
| `RAG_SECTION_MAX_CHARS` | `8000` | 整节文本上限（供 BM25） |
| `RAG_TABLE_ROWS_PER_CHUNK` | `8` | 表格行分块行数 |
| `RAG_HYBRID_DENSE_WEIGHT` | `0.6` | ⚠️ **预留**：当前三路纯 RRF 融合（`Σ1/(rank+RAG_RRF_K)`），无读取者 |
| `RAG_RRF_K` | `60` | RRF 的 k |
| `RAG_BM25_K1` / `RAG_BM25_B` | `1.5` / `0.75` | BM25 参数 |
| `RAG_STRICT_SCOPE` | `true` | 严格限定语料来源（识别出公司/年份即过滤） |
| `RAG_HYDE` | `false` | 查询扩展 / hyDE（未启用分支） |
| `RAG_STRATEGY` | `auto` | `structural` / `factual` / `auto`（意图映射：report→factual、compare→structural） |
| `RAG_TABLE_ENGINE` | `pymupdf` | 表格抽取引擎：`pymupdf` / `camelot` |
| `RAG_HTTP_URL` | `""` | 非空 → 本进程**不走本地 Milvus**，改经 HTTP 从运行中的 web `/api/rag/retrieve` 检索 |
| `RAG_HTTP_TIMEOUT` | `20.0` | HTTP 检索超时（秒） |
| `RAG_HTTP_TOKEN` | `""` | HTTP 检索 Bearer token |

> 内置 `mcp_server/`（本地代理→每接口工具）默认停用，仅作后备；单独跑它时才需 `MCP_SERVER_HOST/PORT` 与 `TUSHARE_PROXY_*`。

> 配置只读 `demo-mcp/.env`（pydantic-settings）。不要提交 `.env`（已被 `.gitignore` 忽略）。

## Web 端功能（`/chat` 为 SSE 流式）

- **流式输出**：`thinking`（模型 reasoning_content，若有）/ `text`（内容增量）/ `tool_call{name,input}` / `tool_result{content,ok}` / `process` / `done` / `error` 事件，末尾 `__end__` 哨兵。
- **思考过程**：`reasoning_content` 在默认展开的「思考过程」面板里逐字流式呈现（顶栏「深度思考」开关会请求 `deepseek-reasoner`，从而真正产生 thinking；注意其工具调用支持取决于 API 版本）。
- **工具调用**：`tool_call` + `tool_result` 渲染为**工具卡**（工具名 + 入参 JSON + 返回结果 + 成功/失败徽章）。工具面为 MCP 服务端暴露的全部接口；RAG 类答案以 Markdown + 内联引用返回。
- **Markdown**：React 前端用 `react-markdown` + `remark-gfm` + `remark-breaks` 渲染，经 `@tailwindcss/typography`（prose）排版表格/标题/列表/代码块（默认不解析原始 HTML，天然防 XSS；表格窄屏可横向滚动，深色 invert 适配）。
- **思考 / 引用**：`process` 事件（`intent`/`rewrite`/`stage`/`retrieval`/`funnel`/`plan`/`params`/`validation`/`aggregate`）进入**默认折叠的「思考过程」**；`done.structured.sources/citations/claims` 在回答底部渲染**引用溯源卡**。
- **日志/恢复**：侧栏列出历史会话（`GET /api/sessions`），点开看消息（`GET /api/sessions/{id}`）、每轮 UI 数据（`GET /api/sessions/{id}/turns`）并可**继续**（服务端用完整上下文恢复）；也可删除（`DELETE /api/sessions/{id}`）。出错时工具 `is_error` 与 agent 异常以 `role=tool/error` 落库，侧栏可定位。
- **RAG HTTP 服务**：同进程挂 `POST /api/rag/retrieve`（body = `RetrievalPlan`）与 `GET /api/rag/health`——供其它进程（CLI/脚本）经 `RAG_HTTP_URL` 取数，也便于前端诊断索引就绪状态。

## RAG 财报知识库（`demomcp/rag/`，已接入实时循环）

针对 A 股年报 PDF 的**问答型** RAG，离线优先、可评估、**已接入 agent 循环**（`tool_rag` 节点并行「选工具执行 + RAG 检索」，检索结果转成证据与确定性引用；`market` 意图跳过 RAG）。完整管线：

- **摄取（离线，逐文档）**：`pdf_parser`（PyMuPDF 解析 → 文本/图片/表格块）→ `section_tree`（字号聚类 + 编号正则检测章节，去目录页）→ `segments`（块归类、图片交 captioner、表格行分块）→ `chunking`（句感知稠密分块 + 表格独立成块）→ `embedder`（bge-m3 服务端 API / hashing）+ `store`（块稠密 + 节稠密 → **Milvus-Lite**）& `bm25`（jieba 词元、节级倒排）→ `persist`（SQLite RelStore 落盘 BM25 状态与元数据）。
- **检索（在线）**：`rewrite_query` 改写查询（公司/财年/术语）→ `tool_rag._rag_retrieve` 构造 `RetrievalPlan`（rewritten_query + concepts + `infer_filters` 公司/年过滤 + strategy）→ `hybrid_retriever`（**三路 RRF**：节稠密 + 节 BM25 + 块稠密，`strategy!=factual` 时后两路参与 → 按 `(doc, section)` 聚合并取 top 5 节 → 每节 top-k 进重排池）→ `reranker`（服务端模型，无 key 则回退 RRF 序）→ 阈值截断 + `top_k` → 带原文来源的 `RagChunk`。
- **运行时**：`rag/runtime.build_runtime_retriever(cfg)` 进程级懒加载（`has_index` → `load_index` 零 API 快载；否则仅当 `RAG_CORPUS_DIR` 显式设置才摄取）；构建失败返回 `None`（无 RAG、不崩）。`RAG_HTTP_URL` 非空时改用 `HttpRetriever`（多进程/单写者场景）。
- **确定性引用**：`citing` 把 `RagChunk` 转成内联标记 `[比亚迪·2025年报 - 第42页 3.2 研发投入]` 与参考列表（**不靠 LLM 编页码**），页面号来自章节树/表格区块的页段；`structured.sources/citations/claims` 随 `done` 事件 + `chat_turn_data` 落库。

**默认离线、100% 确定性**：`HashingEmbedder`（blake2b 特征哈希）+ `InMemoryVectorStore`（余弦）+ 自包含 BM25 + `NoopReranker` + `NoopCaptioner`——不联网、无模型权重、不需要 `rag-full`。`rag/__init__.py` 故意不 import，重依赖（`pymupdf`/`pymilvus`/`milvus-lite`）在函数体内懒加载，由 `tests/test_rag_import_guards.py` 守住。

> 遗留：`demomcp/rag/retriever.py` 的 `NullRetriever` 为历史占位、**无任何引用**（现役后端为 runtime / HttpRetriever + HybridRetriever）。
>
> 设计蓝图与评估动机见 `docs/RAG_FINANCE.md`；全部运行细节与全量配置表见 `docs/RAG_INTEGRATION.md`。

## 离线测试（无需代理 / key / 网络）

```bash
cd demo-mcp
.venv/Scripts/python.exe -m pytest tests -q       # 或 .venv/bin/python
```

覆盖：

- **主链路**：agent 循环全链路（`FakeToolProvider` + `MockLLM`）、LangGraph 五节点路由（越界 → `fallback`、双空 → `no_evidence`）、`code!=0` 作为正常结果、工具异常转 `is_error`、MCP 重试/超时。
- **MCP 取数**：`test_tool_provider.py`（`MCPToolProvider` 原始 MCP：重试/超时/`code!=0` 业务结果）。原先的语义工具层（`test_stock_input` / `test_stock_provider`）已随该层移除而删除。
- **RAG**：20 个 `test_rag_*.py`（PDF 解析、章节树、表格分块、BM25、向量库、重排、引用、摄取幂等、RelStore 持久化、HTTP 检索器、server 助手、funnel 事件、真实 PDF 端到端、sparse import 守卫、离线评估门禁）。
- **配置/数据层**：默认值/路径/DB URL；内存 SQLite 的 `ChatMessage` 日志 + `ChatTurn` 恢复 + `ChatTurnData` 往返。

> 一次完整实测（168 passed、RAG 离线/真引擎指标、端到端冒烟）记录见 `docs/TEST_REPORT.md`。

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
| `agents` | LangGraph 五节点编排、Agent 薄壳 | 改 `agent.py` / `graph/*` |
| `graph` | 意图路由 → 改写 → 工具+检索并行 → 综合/兜底 | 改 `nodes.py` 或加新节点 |
| `interfaces` | 类型 + 两协议 | 改协议即改所有适配器（谨慎） |
| `providers/tools` | 工具来源（MCP / 假 / 语义层 stocks） | 加 `xxx.py`；语义层改 `stocks.py` |
| `providers/llm` | 具体 LLM 后端 | 加 `xxx.py` 实现 `interfaces.llm_client` |
| `rag` | 财报知识库（解析/检索/引用） | 改 `rag/*`；换检索后端只需在 `runtime`/`HttpRetriever` 处注入（`NullRetriever` 占位已无引用） |
| `db` | 会话历史持久化 | 加模型 / 扩展 `store.py` |
| `config` | 环境变量、路径、透传参数 | 加字段即可 |
