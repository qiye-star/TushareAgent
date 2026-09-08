# demo-mcp 项目介绍

> 本文是面向新人和 AI 助手的项目速览。权威细节以 [CLAUDE.md](../CLAUDE.md)（全量约定与坑）、[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)（系统架构）与 [docs/RAG_INTEGRATION.md](../docs/RAG_INTEGRATION.md)（RAG 运行细节）为准。

## 这是什么

**demo-mcp**（包名 `demomcp`）是一个 LLM + MCP 数据对话助手：用户用自然语言提问，内置智能体自行决定调用哪个工具，经 **Tushare 官方 MCP** 取数，并**并行检索 A 股年报 RAG 知识库**，最终综合成带**确定性引用**的中文回答。支持 CLI 与 Web 控制台（SSE 流式输出、思考轨迹、工具卡、会话日志与恢复）。

- **LLM 后端**：DeepSeek（OpenAI 兼容端点，可换）。
- **编排**：LangGraph 状态机，五节点 + 条件自环（见下）。
- **RAG**：面向年报 PDF 的问答型知识库，离线优先、可评估、已接入实时循环。
- **快报（quickreport）**：独立的确定性六段式日报，**不经 LLM**，与主链路解耦。
- **可选第二数据源**：万得（Wind）。
- **形态**：CLI（Telegram 式终端对话）/ Web（React + TS + Tailwind + Vite 前端，由后端同源托管）/ Docker 一键部署。

## 核心链路

```
用户提问
  → router         意图分类 + 越界判断（越界走 fallback）
  → rewrite_query  RAG 查询改写（公司/财年/术语）
  → tool_rag       LLM 选工具 + 并行 RAG 检索（RetrievalPlan）
      │             └─ agentic tool loop（条件自环，上限 DEMO_MAX_ITERATIONS=10）：
      │                每轮执行后 LLM 判定数据是否足够，足够即停止，仍缺就续调下一批；
      │                进展守卫：连续无新增证据（no_progress_cap=2）即终止。
      ▼
  → synthesizer    整合 + 引用 + 免责声明     （无证据 / 越界 → fallback）
```

关键机制：

- **取数面全开**：直接用原始 `MCPToolProvider`，可查任意 A 股 / 任意接口，无语义白名单层；`code!=0`（权限/积分不足）视为业务结果（`is_error=False`）让 LLM 转述，真异常才 `is_error=True`。
- **选工具只喂相关子集**：`tool_select.select_tools` 恒保留 meta 三件套 + 按金融词汇打分 top-K（`TOOL_MAX_REVEALED`），执行仍走全量。
- **错误永不崩图**：节点与 `Agent.run` 只捕 `Exception`（**不捕** `BaseException`，`asyncio.CancelledError` 照常透传），工具错误一律转 `is_error` ToolResult。
- **RAG**：五节点之外独立存在；`market` 意图跳过检索；三路 RRF（节稠密 + 节 BM25 + 块稠密）→ 聚类重排 → 阈值截断 → `RagChunk`；引用编号由 `citing` 确定性生成（不编页码）。

## 目录地图

| 路径 | 职责 |
|---|---|
| `demomcp/interfaces/` | 契约层：类型 + `ToolProvider`/`LLMClient` 两协议（纯契约） |
| `demomcp/agents/` | `Agent` 薄壳：构图 + `ainvoke` + 归一化 `AgentResult` |
| `demomcp/graph/` | 五节点实现（`nodes.py`）+ 路由（`routes.py`）+ 动态工具目录（`tool_select.py`）+ 报告 skill 注册表（`skills.py` / `tracker_render.py`） |
| `demomcp/providers/` | 可插拔适配器：tools（`mcp` 官方 / `wind` 万得 / `composite` 多源合并 / `fake` 假 / `curate` 探测）与 llm（`deepseek` / `mock`） |
| `demomcp/rag/` | 财报知识库：PDF 解析 → 章节树 → 分块 → 混合检索 → 确定性引用；`runtime`/`http_retriever`/`server` 运行时注入 |
| `demomcp/quickreport/` | 快报：配置 / 阈值谓词 / 取数 / 计算投影 / 持久化 / 定时 / 薄壳（`pipeline` 为核心） |
| `demomcp/db/` | SQLAlchemy 2.0 异步：`ChatMessage`（可读日志）+ `ChatTurn`（精确恢复）+ `ChatTurnData`（每轮 UI 载荷） |
| `demomcp/entry/` | `cli.py` / `web.py`（SSE + `/api/sessions` + `/api/quickreport/*` + RAG HTTP 端点 + lifespan 工具池/快报任务） |
| `demomcp/config/` | `Settings` + `PROJECT_ROOT` + `cn_tz()`（叶子层） |
| `mcp_server/` | 内置数据 server（每接口一个工具），**默认停用、仅后备** |
| `scripts/` | `smoke_e2e.py` / `eval_rag.py` / `validate_rag.py` / `dba_rag.py` / `generate_quickreport.py` / `build_quickreport_watchlist.py` |
| `scripts/web/` | React + TS + Tailwind v4 + Vite + Zustand 前端（构建产物 `dist` 由 `web.py` 托管） |
| `tests/` | 离线 gate：`FakeToolProvider` + `MockLLM` + 内存 SQLite，无需网络/key |
| `data/quickreport/` | `watchlist.json`（标的池配置，提交）+ `latest.json` / `history/`（运行时产物，gitignore） |
| `data/vectorstore/` | RAG 索引（Milvus-Lite + SQLite），运行时产物 |

## 快报（quickreport）

独立于 Agent 主链路的**确定性**日度报告：板块概览 → 标的池行情速览 → 关键公告 → 业绩预告异动（阈值 >50 / <-20）→ 产业链催化事件 → 一句话研判（≤150 字）。管线 `server.generate_from` → `pipeline.build_report`（段级降级链 + Semaphore(4) + `wait_for(60s)`）→ `projection` 纯计算 → `store.save_report`。所有数值「数值+文本」双份（`pct`+`pct_text`），段状态三态：`ok` / `empty`（链通但本期无）/ `na`（未接入）。数据源按 2026-09-05 实测权限矩阵落地（如公告用 `anns_d` 已弃、改类型化组合源；行情走全市场快照 `daily(trade_date=…)` 按池滤，**不**逐标的逗号串）。标的池 211 只。

## 快速上手

```bash
# 安装（生成 .venv + uv.lock；Windows venv 无 pip，一律用 uv）
uv sync                                  # 或 uv sync --extra rag-full（RAG 重依赖） / --no-dev（容器）

# 运行（pyproject 无 console_scripts，必须 -m）
uv run python -m demomcp.entry.cli                # CLI 对话
uv run uvicorn demomcp.entry.web:app --port 8010  # Web（先构建前端；SSE 流式）

# 前端（开发模式：Vite 代理 /chat、/api 到 8010）
cd scripts/web && npm ci && npm run build && cd ../..
cd scripts/web && npm run dev

# 测试 / 静态检查（当前门禁：ruff 干净 + pytest 全绿）
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py::test_agentic_loop_two_round_then_synthesize
.venv/Scripts/ruff.exe check demomcp tests scripts

# Docker（一键 Web；两阶段构建 node→python）
docker compose up
```

`.env` 必填：`DS_API_KEY`（DeepSeek）+ `TUSHARE_MCP_URL`（官方 MCP 地址，token 放 URL query，如 `https://api.tushare.pro/mcp/?token=...`）。可选：`WIND_API_KEY`/`WIND_ENABLED`（万得第二源）、`RAG_*`（真实嵌入/重排）、`QUICKREPORT_*`（快报开关/目录/超时/并发）。

## 关键约定（速查）

- **数据源**：默认直连 Tushare 官方 MCP（token 在 URL query）；内置 `mcp_server/` 后备默认停用。
- **工具池生命周期**（web）：lifespan 热启动（`_hot_start_tools`，失败 5s→60s 退避重试）→ 保活 ping 每 45s → 传输失败单飞重建 → 每 30 分钟整池重建（旧池宽限 240s 后关闭）。`TOOL_POOL_HOT_START=false` 恢复冷启动。
- **超时阶梯**：LLM read 180s < SSE 看门狗 `AGENT_IDLE_TIMEOUT=240s`（防前端无限转圈）；改一层连着改。
- **证据双份**：`content`（截到 2000 字符，喂 LLM）/ `raw`（截到 20000，只进 `structured.sources[].data` 供前端表格）——别混用。
- **Windows 时区**：无系统 tz 库，`ZoneInfo` 直接抛——统一走 `config.cn_tz()`（固定 UTC+8），不要直接 `ZoneInfo(...)`。
- **RAG 多进程**：Milvus-Lite 单进程独占锁——其它进程设 `RAG_HTTP_URL` 走 HTTP 检索，不要开本地索引。
- **前端**：SSE 用 POST + `ReadableStream`（非 `EventSource`）；`lib/table.ts` 的 `parseToolTable` 是全站唯一表格解析器，解析失败一律回退 `<pre>` 原文（安全退化）；流式每帧重渲染 Markdown，回调别做重活。
- **测试**：新测试一律离线（`FakeToolProvider` + `MockLLM`），web 相关测试记得置 `TOOL_POOL_HOT_START=false`（快报断言用 `QUICKREPORT_AUTO=false`）；日期断言相对 `datetime.now(cn_tz())` 计算，别硬编码日历日。

## 质量现状（2026-09-07 实测）

- pytest：**308 passed / 3 skipped**；ruff：**All checks passed**。
- pyright 不是可用门禁（依赖未装 rag-full 时误报；需 `--venvpath .`）。
- 历史：169 项零失败 + RAG 门禁全达标 + E2E 逐位一致（2026-08-27，见 [docs/TEST_REPORT.md](../docs/TEST_REPORT.md)）。
