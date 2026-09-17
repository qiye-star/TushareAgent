# demo-mcp：LLM + MCP 多源金融数据对话助手

用自然语言提问，内置智能体自行决定调用哪个工具、经**独立部署的 MCP 网关**（聚合 Tushare 官方 MCP / 万得 Wind / 同花顺 iFind / AkShare / 财经新闻五个源，按源开关）取数，并**并行检索年报 RAG**，最后综合成**带确定性引用**的中文回答——支持 **Web 控制台**（SSE 流式、思考轨迹、工具卡、引用溯源、会话恢复）与 **CLI**。

除了对话，还有两条与主链路解耦的能力：

- **报告技能库（64 条）**：1 条内置产业链快报 + 63 条从 claude-for 投研插件生态反向导入的 `SKILL.md`（投行/私募/财富管理/基金运营/企业财务六域），命中后接管系统提示词与取数偏好，前端有技能广场与「快速使用」。
- **快报仪表盘**：**不经 LLM** 的确定性六段式日度跟踪报告（板块 / 标的池行情 / 关键公告 / 业绩预告异动 / 产业链催化 / 一句话研判），每日 08:30（北京时间）自动生成，前端用 ECharts 渲染成仪表盘，并带数据源与新鲜度看板。

> **当前质量状态（2026-09-12 实测）**：`pytest` **500 passed / 3 skipped**；`ruff` 全仓干净（`demomcp mcp_gateway tests scripts external_sources`）。
> `pyright` **不是**提交门禁（`--venvpath .` 下约 46 个告警，多数源于未安装 `rag-full` 可选依赖，属预期）。

---

## 1. 进程拓扑：取数已完全网关化

```
浏览器 ──HTTP/SSE──► demo (:8010, demomcp)
                         │  一条 MCP 客户端连接（保活 + 断线重建）
                         ▼
                  mcp-gateway (:8766)   ← 唯一持有上游凭证的进程
                         │
        ┌────────────┬───┴────┬──────────────┬─────────────┐
        ▼            ▼        ▼              ▼             ▼
  Tushare 官方 MCP  万得 Wind  同花顺 iFind   AkShare        财经新闻
  (247 个接口)      (7 域)     (7 域)         (独立进程)     (独立进程)
```

- **`demomcp` 不直连任何数据源**，只经 `MCP_GATEWAY_URL`（默认 `http://127.0.0.1:8766/mcp`）当网关的一个纯 MCP 客户端。启动 demo 不会带起任何上游连接。
- **上游地址与 key 只配在 `mcp_gateway/.env` 一处**，并可在前端「设置 → 数据源明细」按源开关（`POST /admin/sources/{id}`），改开关**无需重启网关**。
- 两者**各自独立启停**、故意不设 `depends_on`：停掉网关，demo 照常在跑（页面显示「网关不可达」、对话退化为纯 LLM）；停掉 demo，网关照常对外提供 MCP。
- 内置 `mcp_server/`（本地 Tushare 代理，`:8765`）是**另一个东西**——历史遗留后备通道，profile 门控、默认不启动，别和网关混淆。

**实测工具面**：Tushare 247 + Wind 3（懒发现元工具，内部编目 35 个接口）+ iFind 3（元工具，内部编目 32 个接口）+ AkShare 9 + 财经新闻 2。
**各源之间没有自动降级**：工具名互不重叠（`wind_*` / `ifind_*` / Tushare 原生名 / 免费源原生名），网关的路由是「工具名 → 唯一源」。跨源取舍发生在 agent 选工具那一步（某源报无权限 → LLM 改选另一源的等价接口）。

---

## 2. 快速开始（本地，两个进程）

### 2.1 前置

- Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node 20+（构建前端）
- 一个 **Tushare 官方 MCP 地址**（`https://api.tushare.pro/mcp/?token=...`，token 放 URL query）
- 一个 **DeepSeek API key**（或任何 OpenAI 兼容端点）
- 可选：万得 key / 同花顺 iFind token / 免费源（各自可留空，留空即不注册该源）

### 2.2 安装与配置

```bash
uv sync                                          # 生成本项目自己的 .venv + uv.lock
cp .env.example .env                             # 填 DS_API_KEY（必填），其余可用默认
cp mcp_gateway/.env.example mcp_gateway/.env     # 填 TUSHARE_MCP_URL（含 token）等上游凭证
cd scripts/web && npm ci && npm run build && cd ../..   # 构建前端（不构建则 Web 只有 API、没有页面）
```

> **两份 .env 的边界是刻意的**：根 `.env` 只管 LLM / 会话库 / RAG / 快报；`mcp_gateway/.env` 只管上游源。在根 `.env` 里配 `TUSHARE_MCP_URL` 不会生效。

### 2.3 启动

```bash
# Windows：一键起两个独立进程（网关带自动重启 + Web）
.\scripts\dev_up.ps1
#   .\scripts\dev_up.ps1 -GatewayOnly / -WebOnly 可单独起

# 或手动，各开一个终端：
uv run uvicorn mcp_gateway.app:app --port 8766     # 网关
uv run uvicorn demomcp.entry.web:app --port 8010   # Web 控制台 → http://127.0.0.1:8010

# CLI 对话（同样经网关取数）
uv run python -m demomcp.entry.cli

# 前端开发模式（Vite 代理 /chat、/api 到 8010）
cd scripts/web && npm run dev
```

> `pyproject.toml` 里**没有** console_scripts，必须 `uv run python -m demomcp.entry.cli`，不能 `uv run demomcp.entry.cli`。
> Windows 的 venv 里没有 pip，一律用 `uv`（`uv sync` / `uv run` / `uv pip install`）。

### 2.4 验证

```bash
curl http://127.0.0.1:8766/admin/health      # 网关：{"ok":true,"config_problems":[],"sources":[…]}
curl http://127.0.0.1:8766/admin/sources     # 每个源的 enabled / connected / tool_count
curl http://127.0.0.1:8010/api/sessions      # 返回 [] 即 Web 就绪
```

示例提问：`比亚迪最近一个月的日线` / `贵州茅台近两年的营收` / `写一份寒武纪业绩点评报告`。
标的与接口**不受限**，可查任意 A 股 / 指数 / 任意接口。

---

## 3. Docker 运行

```bash
cp .env.example .env                             # DS_API_KEY 等
cp mcp_gateway/.env.example mcp_gateway/.env     # 上游凭证（**必需文件**，缺了 compose 直接报 env file not found）
docker compose up                                # 起 demo:8010 + mcp-gateway:8766 两个独立容器
```

- **两阶段构建**：`node:20-alpine` 出 `scripts/web/dist` → `python:3.11-slim` 装 uv 运行时；`web.py` 同源托管前端产物。
- `demo` 容器的 `MCP_GATEWAY_URL` 已在 compose 里指向 `http://mcp-gateway:8766/mcp`。
- 卷：`demo_data`（`demo.db` + `data/settings/`）、`mcp_gateway_data`（按源开关 `sources.json`）；本地 `./data/vectorstore` 与 `./docs` 以 bind-mount 共享给容器（RAG 索引不用重灌）。
- `docker compose stop mcp-gateway` 只停网关，demo 不受影响——这就是解耦的可观察形态。
- 历史遗留的 `mcp-server` service 由 profile 门控，默认不启动：`docker compose --profile mcp-server up -d`。

---

## 4. 架构（分层严格、依赖单向）

```
demo-mcp/
├── demomcp/                 主应用包（不直连数据源）
│   ├── interfaces/          契约层：类型 + ToolProvider / LLMClient 两协议（纯契约，无实现）
│   ├── agents/              薄壳：Agent（构图 + ainvoke + 归一化 AgentResult + 按源注入用法提示词）
│   ├── graph/               LangGraph 五节点 + 动态工具目录 + 技能注册表/装配器
│   ├── providers/           可插拔适配器（tools: mcp/wind/composite/curate/fake；llm: deepseek/mock）
│   ├── rag/                 年报知识库（解析/混合检索/确定性引用/持久化），离线优先
│   ├── skill_library/       vendored 的 claude-for 语料（63 个 SKILL.md）
│   ├── quickreport/         快报：确定性取数 + 计算，**不经 LLM**，与主链路解耦
│   ├── config/              Settings + PROJECT_ROOT + 总闸/技能开关
│   ├── db/                  SQLAlchemy 2.0 异步：ChatMessage / ChatTurn / ChatTurnData
│   └── entry/               cli.py / web.py（SSE + REST）
├── mcp_gateway/             **独立进程/端口（:8766）**：唯一持有上游源的服务（见其 README.md）
├── external_sources/        免费源 MCP server（AkShare / 财经新闻）：独立进程 + 独立 requirements.txt
├── mcp_server/              内置 Tushare 代理（:8765，后备、默认停用）
├── scripts/web/             React + TS + Tailwind v4 + Vite + Zustand 前端
├── scripts/                 运维脚本（见 §9）
├── tests/                   62 个离线测试文件（无需 key / 网络）
└── docs/                    设计与评估文档（见 §10）
```

依赖方向：`entry → agents → interfaces`；`providers → interfaces`；`config` 为叶子；`db` 被 entry 使用；`rag` 经 `Agent._get_retriever` 注入图；`quickreport` 不 import `agents/*`。

### 4.1 核心链路（LangGraph 五节点）

```
router ──► rewrite_query ──► tool_rag ──(条件自环，多轮取数)──► synthesizer
   │                            │                                  ▲
   └─ 越界 ────────────────► fallback ◄── 工具与 rag_chunks 双空 ───┘
```

- **`router`**：意图分类（market / report / compare / …）+ 越界判断 + 技能匹配。
- **`rewrite_query`**：RAG 查询改写（不需要 RAG 的意图直接跳过，省一次 LLM 往返）。
- **`tool_rag`**：**并行**执行「LLM 选工具 + 逐个 `call_tool`」与「RAG 检索」——RAG 检索在选工具的 LLM 调用**之前**就 `create_task` 发起，两段耗时重叠而非相加；单个工具调用有护栏超时 `DEMO_TOOL_CALL_TIMEOUT`（默认 90s），防一个慢工具拖住整轮 `gather`。
- **agentic tool loop**：每轮执行后 LLM 看返回判定数据是否足够——足够就停止去 `synthesizer`，仍缺就续调下一批工具，上限 `DEMO_MAX_ITERATIONS`（默认 10）。**进展守卫**：连续 2 轮没有新增任何证据即终止，防「无权限 / 空数据」时空转烧满轮次。
- **`synthesizer`**：整合证据 + 确定性引用 + 免责声明；命中技能时由技能的 `system_prompt` / `render` 接管。
- **模型分工**：前端「深度思考」开关只作用于**最终 synthesizer**（`deepseek-reasoner`）；router / rewrite_query / 选工具三步用快模型——这三步要么不展示推理过程、要么是循环体，套用深度思考纯粹拉长延迟。

**每轮循环都可视化**：节点经 `on_process` 抛 `loop_turn{round,status,tools,evidence}`（status ∈ continue / stop / max-reached / no-progress），前端据此按轮渲染思考轨迹。

### 4.2 工具面收窄（防上下文爆满）

选工具那一轮只把**相关子集**喂给 LLM（`graph/tool_select.select_tools`）：

- 恒保留 meta 三件套 `list_apis` / `get_api_info` / `query` + `stock_basic`；
- 其余按金融词汇重叠打分取 top-K（`TOOL_MAX_REVEALED`，默认 12）；
- **`REPORT_BASELINE_FAMILIES`**（利润表 / 资产负债表 / 现金流量表 / 财务指标 / 估值 / 业绩预告快报）在 report / compare 意图或命中技能时**恒揭示**——修掉此前「问法字面没命中关键词 → 财务接口零揭示」的真实 bug；
- 技能声明的 `tool_families` 同样恒揭示，不占 top-K 限额；
- **执行不受限**：`tools.call_tool` 走全量 provider。

### 4.3 多期财务数据表格化

官方接口的多期财报是一个按 `end_date` 排列的数组。按字符位置截断会把用户实际问到的报告期折进匿名的「中间省略」里，模型随后用邻近可见期的数值顶替、换个标签充数（真实复现：宁德时代 2025H1/Q1 被填成 2024 同期的真实数值）。现在命中 `end_date` 报告期数组时走 `_render_period_table`：`_period_label`（YYYYMMDD → Q1/H1/前三季度/年报）+ `_dedupe_period_rows`（按 `end_date+report_type` 去重）+ 确定性 Markdown 表格，**按整期截断而非按字符位置**，每期数值紧邻自己的标签。

---

## 5. Web 端功能

### 5.1 对话（`POST /chat`，SSE）

- 事件：`thinking`（reasoning_content）/ `text` / `tool_call{name,input}` / `tool_result{content,ok}` / `process` / `done` / `error`，末尾 `__end__` 哨兵。
- `process.kind` ∈ `intent` / `rewrite` / `stage` / `retrieval` / `funnel` / `plan` / `params` / `validation` / `aggregate` / `loop_turn`。
- `ChatRequest` 支持 `model`（「深度思考」传 `deepseek-reasoner`）、`mode`（`quick` / `agent`）、`skill`（技能页「快速使用」指定的 skill id）。
- 前端用 `react-markdown` + `remark-gfm` + `remark-breaks` 渲染（默认不解析原始 HTML，天然防 XSS）。
- **SSE 一定收敛**：`AGENT_IDLE_TIMEOUT = 240s` 完全静默看门狗，到点取消任务并发 `error` + `__end__`，不会前端无限转圈。

### 5.2 REST

| 端点 | 说明 |
|---|---|
| `GET\|DELETE /api/sessions`、`GET /api/sessions/{id}`、`GET /api/sessions/{id}/turns` | 会话列表 / 消息 / 每轮 UI 载荷；可从历史**精确恢复**（`ChatTurn.messages_json` 还原 tool_calls/tool_call_id） |
| `GET\|POST /api/settings/mcp` | 取数**总闸**（关掉 → 连网关都不连，退化为纯 LLM 聊天），持久化在 `data/settings/mcp_toggle.json` |
| `GET /api/settings/mcp/sources`、`POST /api/settings/mcp/sources/{id}` | 转发网关的按源开关；网关不可达时返回诊断而非 500 |
| `GET /api/skills`、`GET /api/skills/{id}`、`POST /api/skills/{id}/toggle` | 技能广场：清单 / 详情 / 按技能开关（`data/settings/skills.json`，改完即生效、无需重启） |
| `GET /api/quickreport/latest`、`POST /api/quickreport/generate` | 快报：读最新 / 手动生成 |
| `GET /api/quickreport/status` | **纯读**的健康看板：数据新鲜度、各段来源、`server_time` / `next_run_at`、`missing_required`、`last_error`——不触发任何 MCP 连接，可秒级轮询 |
| `GET /api/quickreport/history`、`GET /api/quickreport/report/{YYYY-MM-DD}` | 历史存档列表 / 按日回看 |
| `POST /api/rag/retrieve`、`GET /api/rag/health` | RAG HTTP 服务（供其它进程经 `RAG_HTTP_URL` 复用索引） |

### 5.3 前端（`scripts/web/`）

React + TS + Tailwind v4 + Vite + Zustand。Sidebar 四视图切换：**聊天 / 快报 / 技能 / 设置**。

- `components/agent/*`：思考轨迹、工具卡、引用溯源列表、检索漏斗。
- `components/report/*`：快报仪表盘——头部条 + KPI 指标行 + 左右分栏（左主区图表/表格、右栏研判与数据源看板），`panels/` 一段一卡，`charts/` 是 ECharts option 构造器。
- `components/charts/echarts-setup.ts` 是全站唯一的 ECharts 注册点（只 `use()` 用到的组件，控制包体）。
- `lib/table.ts::parseToolTable` 是**全站唯一**的工具返回表格解析器（引用来源卡与工具卡共用），认 4 种形态，解析不了一律回退 `<pre>` 原文——安全退化，绝不空白。

---

## 6. RAG 年报知识库（`demomcp/rag/`）

针对 A 股年报 PDF 的问答型 RAG，**离线优先、独立于主链路**。

- **摄取**：`pdf_parser`（PyMuPDF）→ `section_tree`（字号聚类 + 编号正则，去目录页）→ `segments`（图片交 captioner、表格行分块）→ `chunking`（句感知稠密分块）→ `embedder` + `store`（Milvus-Lite）& `bm25`（jieba）→ `persist`（SQLite 关系库）。
- **检索**：`query_build` → `hybrid_retriever`（**三路 RRF**：节稠密 + 节 BM25 + 块稠密 → 按 `(doc,section)` 聚类 → top 节 → 重排池 → `reranker` → 阈值截断 + `top_k`）。`strategy` 可收窄：`factual` 只走 chunk 路、`structural` 只走节级两路、`auto` 全量。
- **确定性引用**：`citing` 生成内联标记 `[比亚迪·2025年报 - 第42页 3.2 研发投入]` 与参考列表，页码来自章节树，**不靠 LLM 编**。
- **默认 100% 离线确定性**：`HashingEmbedder` + `InMemoryVectorStore` + 自包含 BM25 + Noop 重排/captioner，不联网、不需要 `rag-full`、不需要 key。
- **只在解析出语料公司时检索**：无公司过滤时直接空返，避免把别家年报切片搜出来污染答案。

```bash
uv sync --extra rag-full                     # 真实后端依赖（pymilvus / milvus-lite / pymupdf / jieba / rank-bm25）
uv run scripts/dba_rag.py                    # 建索引（增量；--rebuild 全重建）
uv run scripts/eval_rag.py                   # 离线评估（默认合成语料，不联网）
uv run scripts/eval_rag.py --corpus "docs/比亚迪：2025年年度报告.pdf" --gold tests/rag_golden/qa_real.yaml
uv run scripts/validate_rag.py               # 真引擎门禁（两份真实年报）
```

- 索引落 `data/vectorstore/`（`milvus.db` + `rag_rel.db`）；web 启动按 `has_index` 自动快载。
- **Milvus-Lite 是单进程独占锁**：web 持锁时，CLI / 脚本 / 第二个 worker 请设 `RAG_HTTP_URL=http://127.0.0.1:8010` 走 HTTP 检索，不要直接开本地索引（否则会被静默吞成「无检索」）。
- 评估门禁：`context_recall@k ≥ 0.8 && faithfulness ≥ 0.7 && cross_company_isolation == 1.0`（退出码 0/1）。

---

## 7. 报告技能库（64 条）

`SKILLS` = 1 条内置（`ai_supply_chain_tracker`，走 `tracker_render` 确定性六段模板）+ 63 条 claude-for 技能（六域：china-finance / investment-banking / private-equity / wealth-management / fund-admin / operations）。

- **正文按小节拆两路**：`Data Sources` / `Workflow` / `Key Terms` 等取数类 → `tool_hint`（进 `tool_rag`，硬上限 3000 字符，因为多轮循环每轮都带）；`Purpose` / `Output` / 写作类 `Step N` → `system_prompt`（进 `synthesizer`）。
- **`system_prompt` 拼接顺序即优先级**：`base` → 本项目输出纪律段 → 工具名对照段 → 能力降级段 → 技能正文。纪律段不是可选文案——它整体替换 synthesizer 的系统提示词，不包这一层就会丢掉反编造纪律。
- **工具名对照**：技能正文里的 `wind_*` / `ifind_*` 是 claude-for 环境的具名工具，本项目只有懒发现元工具 → 自动生成「旧名 → `wind_query(api_name=…)` / `ifind_query(query=…)`」对照。
- **router 清单用中文短描述**：63 条原始英文 description 合计 20.7KB，而 router 每次提问都跑一次 → `skill_catalog.CATALOG` 人工维护「中文短名 + ≤40 字」，整张清单 ≈4.4KB（测试守住 6KB 上限）。
- **按技能开关**：`data/settings/skills.json`，生效点是 router 每次现读，改完即生效。
- **「快速使用」= `forced_skill`**：此时 router 不拼技能清单（省 ~4.4KB/次），只判 intent + 越界。
- **逃生开关**：`SKILL_LIBRARY_ENABLED=false` → 只剩内置 1 条。
- 语料 vendored 进包内；`uv run scripts/sync_claude_for_skills.py --src <path> [--dry-run]` 只在开发期刷新。

---

## 8. 快报（`demomcp/quickreport/`）

**不经 LLM** 的确定性日度报告：`call_tool` 直取 → 阈值 / 拼接计算 → 前端渲染。

- **管线**：`server.generate_from`（web 端点 / 定时任务 / 独立脚本共用入口）→ `pipeline.build_report`（五段 `asyncio.gather` 并行 + 段内降级链 + `Semaphore(4)` 限流 + 段级 60s 超时）→ `projection`（纯计算）→ `store.save_report`（`latest.json` + 按日存档 `history/{YYYY-MM-DD}.json`）。
- **JSON 契约**：段 `status ∈ ok | empty | na`（`na` = 未接入/全败，`empty` = 链通但本期无，如非交易日）；数值一律「数值 + 文本」双份（`pct` + `pct_text`）；顶层 `missing[] / errors[] / provenance`。
- **来源标注**：每段带 provenance（Tushare / 万得 / iFind / 免费源 / 管线），前端数据源看板直接显示。
- **调度**：每日 08:30（`cn_tz()`，全程北京时间算完再转 UTC sleep）；启动补跑；**缺必需段的报告不算最新**，下次触发会重试而不是整天停在退化态；与手动端点共用一把锁。
- **标的池**：`data/quickreport/watchlist.json`（211 只，提交进仓库），`scripts/build_quickreport_watchlist.py` 生成。
- **离线独立生成**：`uv run scripts/generate_quickreport.py`。
- 关掉每日任务：`QUICKREPORT_AUTO=false`。

---

## 9. 常用命令

```bash
# 测试（Windows exe 在 .venv/Scripts，Linux/容器在 .venv/bin）—— 当前 500 passed / 3 skipped
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -k max_iterations

# 静态检查
.venv/Scripts/ruff.exe check demomcp mcp_gateway tests scripts external_sources   # 干净
PYRIGHT_PYTHON_NODE_VERSION=22.23.2 .venv/Scripts/pyright --venvpath .            # 非门禁，见下

# 真实端到端（需真 DS_API_KEY + 网关已启动且 token 有效）
uv run scripts/smoke_e2e.py

# 工具可用性探测（真实调用，默认关；产出 data/tool_catalog.json）
uv run scripts/probe_tools.py

# 免费源（各自独立进程、独立 requirements.txt，不属于本包）
pip install -r external_sources/requirements.txt
python external_sources/akshare_server.py    --port 8000
python external_sources/china_news_server.py --port 8001
```

> **`pyrightconfig.json` 的 `venvPath: ".."` 已过时**（仓库从子目录挪成根目录后没跟着改）：直接跑 `pyright` 会去上一级找 `.venv`、凭空多报约 31 个 `reportMissingImports`。跑检查请加 `--venvpath .`。真门禁是 `ruff` + `pytest`。

---

## 10. 配置

### 10.1 根 `.env`（LLM / agent / 会话库 / RAG / 快报）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DS_API_KEY` | — | DeepSeek API key（**必填**） |
| `DS_BASE_URL` / `DS_MODEL` | `https://api.deepseek.com` / `deepseek-chat` | OpenAI 兼容端点与模型 id |
| `DS_MAX_TOKENS` / `DS_STREAMING` | `8192` / `true` | 单次回复上限 / 是否流式 |
| `DEMO_SYSTEM_PROMPT` | 内置投研 prompt | 系统提示词 |
| `DEMO_MAX_ITERATIONS` | `10` | agentic tool loop 取数轮次上限 |
| `DISCLAIMER` | 内置 | 追加的免责声明 |
| `DEMO_DATABASE_URL` | 空 → `demo.db` | 会话库；支持 `mysql/asyncmy`、`postgres/asyncpg` |
| `MCP_GATEWAY_URL` | `http://127.0.0.1:8766/mcp` | **唯一取数入口**（容器里是 `http://mcp-gateway:8766/mcp`） |
| `MCP_GATEWAY_ADMIN_URL` | 空 → 自动推导 | 网关管理 REST base（前端按源开关页调用） |
| `DEMO_MCP_TIMEOUT` / `DEMO_MCP_RETRIES` | `30` / `2` | demo → 网关这一跳的读超时 / 重试 |
| `DEMO_TOOL_CALL_TIMEOUT` | `90` | 单个工具调用的护栏超时（`tool_rag` 一轮 gather 内） |
| `DEMO_MCP_KEEPALIVE` | `45` | 保活 ping 间隔（秒）；0 = 关 |
| `TOOL_POOL_HOT_START` | `true` | web 启动即连网关（退避重试）；false = 首个 `/chat` 冷连 |
| `TOOL_MAX_REVEALED` / `TOOL_META_ALWAYS` | `12` / `true` | 每轮揭示给 LLM 的非 meta 工具上限 / 恒保留 meta 三件套 |
| `TOOL_PROBE_ENABLED` / `TOOL_PROBE_CACHE_PATH` | `false` / `data/tool_catalog.json` | 启动时探测接口可用性（默认关，避免烧积分）/ 缓存路径 |
| `QUICKREPORT_ENABLED` / `QUICKREPORT_AUTO` | `true` / `true` | 快报总开关 / 每日定时任务与启动补跑 |
| `QUICKREPORT_DIR` / `_STAGE_TIMEOUT` / `_CONCURRENCY` | `data/quickreport` / `60` / `4` | 数据目录 / 单段超时 / MCP 并行上限 |
| `SKILL_LIBRARY_ENABLED` / `SKILL_LIBRARY_DIR` | `true` / 包内 | 技能库逃生开关 / 语料目录 |
| `LOG_LEVEL` / `CHAT_LOG_PATH` / `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT` | `INFO` / `logs/chat.log` / `5MB` / `3` | 对话文本日志（UTF-8 + 按大小轮转） |

**RAG_\*** 全量配置表见 `.env.example` 与 `docs/RAG_INTEGRATION.md`。要点：`RAG_USE_REAL=false`（默认）时全链路纯 Python 确定性；设 `true` 需 `uv sync --extra rag-full` + `RAG_EMBEDDING_API_KEY`。`RAG_SCORE_THRESHOLD` 与 `RAG_HYBRID_DENSE_WEIGHT` 是**预留项**，当前代码不读取。

### 10.2 `mcp_gateway/.env`（上游源，唯一配置处）

| 变量 | 默认 | 说明 |
|---|---|---|
| `GATEWAY_HOST` / `GATEWAY_PORT` | `0.0.0.0` / `8766` | 监听地址 |
| `GATEWAY_DATA_DIR` | `mcp_gateway/data` | 按源开关 `sources.json` 存放目录 |
| `TUSHARE_MCP_URL` | 空 | Tushare 官方 MCP（**token 放 URL query**）；留空即不注册该源 |
| `WIND_API_KEY` / `WIND_ENABLED` | 空 / `true` | 万得 Wind（`Bearer` 头） |
| `IFIND_AUTH_TOKEN` / `IFIND_ENABLED` / `IFIND_CONCURRENCY` | 空 / `true` / `2` | 同花顺 iFind（**裸 token，无 Bearer**；并发受套餐硬限：免费 2 / 个人 5 / 企业 10） |
| `AKSHARE_MCP_URL` / `CHINA_NEWS_MCP_URL` | 空 | 免费源（各自独立进程，网关只当 MCP 客户端连它们的 URL） |
| `MCP_TIMEOUT` / `MCP_RETRIES` / `MCP_KEEPALIVE` | `30` / `2` / `45` | 每个上游连接的超时 / 重试 / 保活 |

**留空 = 不注册该源**（不会在 `/admin/sources` 里留一个永远连不上的开关项）。配置没配好**不崩进程**——启动 ERROR 日志 + `/admin/health` 的 `config_problems` + 前端「设置」页会照实说缺什么。详见 [`mcp_gateway/README.md`](mcp_gateway/README.md)。

---

## 11. 离线测试

```bash
.venv/Scripts/python.exe -m pytest tests -q     # 500 passed / 3 skipped，无需 key / 代理 / 网络
```

覆盖 62 个测试文件：

- **主链路**：agent 循环全链路（`FakeToolProvider` + `MockLLM`）、五节点路由、多轮取数与进展守卫、直接作答通道、多期财务表格化、synthesizer 拒答/重试/降级。
- **网关**：`test_mcp_gateway_{admin,pool,protocol,proxy_web,sources,toggle_store}.py`——真 MCP 协议往返、按源开关、跨源撞名、池退休。
- **数据源**：`test_{tool_provider,mcp_retry,mcp_reconnect,wind_provider,ifind_provider,source_usage_guides}.py`——重试语义、断线重建、各源信封判据、用法提示词注入点。
- **技能库**：`test_skill_{loader,api}.py`——63 条语料装配、catalog 完整性与 6KB 上限、工具名映射与大小写、按技能开关。
- **快报**：`test_quickreport_{config,pipeline,projection,scheduler,source,store,web}.py`——降级链、阈值、时区、必需段守卫、存档往返。
- **RAG**：20 个 `test_rag_*.py`——解析、章节树、表格分块、BM25、向量库、重排、引用、幂等、持久化、HTTP 检索、import 守卫、离线评估门禁。
- **配置/数据层**：默认值、路径、DB URL、三表往返与历史清洗。

---

## 12. 文档

| 文档 | 状态 | 说明 |
|---|---|---|
| [`mcp_gateway/README.md`](mcp_gateway/README.md) | 当前实现 | **MCP 网关**：五源装配、按源开关、连接池语义、`/admin` API、各源踩坑 |
| [`external_sources/README.md`](external_sources/README.md) | 当前实现 | 免费源 server（AkShare / 财经新闻）独立部署与已知反爬坑 |
| [`docs/SKILLS_IMPORT_PLAN.md`](docs/SKILLS_IMPORT_PLAN.md) | 当前实现 | 技能库导入方案与 13 项决策 |
| [`docs/SKILLS_INTEGRATION_DECISIONS.md`](docs/SKILLS_INTEGRATION_DECISIONS.md) | 当前实现 | 技能库实测事实基线（小节分布、工具名词频等） |
| [`docs/RAG_INTEGRATION.md`](docs/RAG_INTEGRATION.md) | 当前实现 | RAG 集成与运行：检索计划、RRF、重排、持久化、HTTP 服务、全量 `RAG_*` 默认值表 |
| [`docs/RAG_FINANCE.md`](docs/RAG_FINANCE.md) | 设计蓝图 | 为什么混合检索、如何保证引用不编造、评估门禁标准 |
| [`docs/RAG_VECTORDB.md`](docs/RAG_VECTORDB.md) | 设计 | 向量库选型与持久化方案 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | ⚠️ 核对于 2026-08-27 | 五节点状态机与分层仍准确，但**写于网关化之前**——凡提到应用直连 `TUSHARE_MCP_URL` 处以本 README §1 为准 |
| [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md) | 历史记录 | 2026-08-27 的量化评分卡（pytest 169 项时期），仅作历史 |
| [`docs/tool-call-layer.md`](docs/tool-call-layer.md) | 已废弃 | 语义工具层设计；该层已移除，现直达原始 MCP |
| [`CLAUDE.md`](CLAUDE.md) | 当前实现 | 给 Claude Code 的工程约定与**关键坑清单**（改代码前必读） |

> `docs/` 里还有 3 份年报 PDF（RAG 语料样本，非文档）。设计蓝图类文档与代码冲突时，**以代码与本 README 为准**。

---

## 13. 扩展点

| 想做的事 | 改哪里 |
|---|---|
| 接一个新数据源 | `mcp_gateway/sources.py::build_sources()` 加一条 `SourceDef`；池 / 开关 / MCP 端点全部按 source id 泛型处理，不用跟着改。**先确认它的成功码**——不是 `code:0/1` 就要传 `business_error` 判据 |
| 加一个报告技能 | 内置：往 `graph/skills.py::BUILTIN_SKILLS` 加 entry；语料：放进 `skill_library/` 并在 `skill_catalog.CATALOG` 补中文短描述 |
| 换 LLM 后端 | `providers/llm/` 加一个实现 `interfaces.llm_client`；消息帧渲染由 LLM 实现负责，换后端不改图 |
| 改编排 | `graph/nodes.py` / `routes.py` / `builder.py`（五节点 + 条件边） |
| 改检索算法 | `rag/hybrid_retriever.py`；换后端只需在 `runtime` / `HttpRetriever` 处注入 |
| 加快报段 | `quickreport/pipeline.py`（取数 + 降级链）+ `projection.py`（纯计算）+ 前端 `components/report/panels/` |
| 加入口 | `demomcp/entry/` 加一个文件 |

---

## 14. 免责声明

本项目输出的所有内容基于公开数据整理，**仅供研究参考，不构成投资建议**。数据准确性以各数据源服务端返回为准；接口权限、积分与限流由各数据商决定，本项目如实转述其返回，不做任何推断或补全。
