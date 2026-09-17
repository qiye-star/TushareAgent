# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

demo-mcp（包名 `demomcp`）是一个 LLM+MCP 数据对话助手（DeepSeek · MCP · Web/CLI）。用户用自然语言提问，内置智能体自行决定调用哪个工具、经**独立部署的 MCP 网关**（`mcp_gateway/`，聚合 Tushare 官方 MCP + 万得 + 同花顺 iFind + 免费源，按源可开关）取数，并**并行检索年报 RAG**，再总结成带确定性引用的中文回答。

- **本仓库根目录就是项目根**（历史上它曾是某个 tushare-data 仓库下的 `demo-mcp/` 子目录，文档/配置里残留的「父仓库/父应用」措辞已过时；`PROJECT_ROOT = parents[2]` 仍然正确，因为 `demomcp/` 就在根下）。
- **取数已网关化（2026-09-07）**：数据源由**独立进程/端口**的 `mcp_gateway/`（默认 `:8766`）持有——Tushare 官方 MCP（`TUSHARE_MCP_URL`）+ 万得 Wind（`WIND_API_KEY`）+ 同花顺 iFind（`IFIND_AUTH_TOKEN`）+ 两个免费源（`AKSHARE_MCP_URL` / `CHINA_NEWS_MCP_URL`，各自独立进程见 `external_sources/`），**只配在 `mcp_gateway/.env` 一处**，并可**按源开关**（`/admin/sources`，前端「设置」页）。**各源之间没有自动降级**：`pool.py` 的路由是「工具名 → 唯一源」且各源命名互不重叠，所以「同名工具换源重试」不存在；跨源取舍发生在 agent 选工具那一步（某源报无权限 → LLM 改选另一源的等价接口），靠 `base_system_for` 注入的各源用法约定 + `graph/tool_select.py` 驱动。`demomcp` 自己**不直连任何数据源**，只经 `MCP_GATEWAY_URL`（默认 `http://127.0.0.1:8766/mcp`）当网关的一个纯 MCP 客户端 → 启动 demomcp 不会带起任何上游 MCP 连接。两者各自独立启停（`docker compose up` 起两个容器；本地 `scripts/dev_up.ps1` 起两个独立进程）。
- 内置 `mcp_server/server.py`（本地 Tushare 代理 → 每接口一个工具）是**另一个东西**（历史遗留后备通道，`:8765`，profile 门控默认停用），别和网关混淆；单独跑它才需 `TUSHARE_PROXY_*` / `MCP_SERVER_HOST/PORT`。
- 自带 uv 环境（`uv sync` 建本项目自己的 `.venv`）+ Docker（`docker compose up`）+ React 前端（`scripts/web/`，Vite 构建产物由 `web.py` 同源托管）。

## 常用命令

```bash
# 安装（生成 .venv + uv.lock）
uv sync                          # 或 uv sync --no-dev（容器/只装运行时）

# 运行（注意：pyproject 没有 console_scripts，必须 `uv run python -m ...`，不能 `uv run demomcp.entry.cli`）
uv run python -m demomcp.entry.cli                # CLI 对话
uv run uvicorn demomcp.entry.web:app --port 8010  # Web（SSE 流式；先构建前端，见下）

# 前端（React+TS+Tailwind/Vite，产物 scripts/web/dist 由 web.py 静态托管）
cd scripts/web && npm ci && npm run build && cd ../..   # 不构建则 Web 只有 API、没有页面
cd scripts/web && npm run dev                           # 开发模式：Vite 代理 /chat、/api 到 8010

# 测试（Windows exe 在 .venv/Scripts，Linux/容器在 .venv/bin）——当前 500 passed / 3 skipped
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py::test_agentic_loop_two_round_then_synthesize
.venv/Scripts/python.exe -m pytest tests/test_agent_loop.py -k max_iterations

# 静态检查
.venv/Scripts/ruff.exe check demomcp mcp_gateway tests scripts external_sources   # 干净（All checks passed）
PYRIGHT_PYTHON_NODE_VERSION=22.23.2 .venv/Scripts/pyright --venvpath .   # 见下方 venvPath 坑

# Docker（起 demo:8010 + mcp-gateway:8766 两个独立容器；需先备好 mcp_gateway/.env）
docker compose up

# 本地双进程（Windows）：网关（带自动重启）+ Web，各自独立进程
.\scripts\dev_up.ps1
uv run uvicorn mcp_gateway.app:app --port 8766   # 只起网关（另一个终端）

# RAG 离线评估（默认合成语料，不联网；--corpus 对真实 PDF，--gold/--top-k/--no-real 可选；真实 PDF 需 --extra rag-full）
uv run scripts/eval_rag.py
uv run scripts/eval_rag.py --corpus "docs/比亚迪：2025年年度报告.pdf" --gold tests/rag_golden/qa_real.yaml

# （可选）RAG 真实后端依赖：uv sync --extra rag-full

# 真实端到端（需真 DS_API_KEY + **MCP 网关已启动**：mcp_gateway/.env 配好 TUSHARE_MCP_URL 且 token 有效）
uv run scripts/smoke_e2e.py
```

> Windows venv 无 pip；一律用 `uv`（`uv sync`/`uv run`/`uv pip install`）。

## 架构（big picture）

分层严格、依赖单向：`entry → agents → interfaces`；`providers → interfaces`；`config` 为叶子；`db` 被 entry 使用。

```
interfaces  类型 + ToolProvider / LLMClient 两协议（纯契约，无实现）
agents      Agent 薄壳 = LangGraph 五节点编排（router/rewrite_query/tool_rag/synthesizer/fallback，只依赖 interfaces）
graph       五节点实现 + 动态工具目录（tool_select）+ 报告 skill 注册表（skills / tracker_render）
providers   tools: mcp/wind/composite/curate/fake；llm: deepseek/mock —— 可插拔适配器
rag         财报知识库（解析/混合检索/引用/持久化），经 Agent._get_retriever 注入实时图、离线优先
quickreport 快报：六段式高频跟踪报告（确定性取数+计算，**不经 LLM**，与主链路解耦）；见下方专节
config      Settings + PROJECT_ROOT（MCP URL / DISCLAIMER / RAG rag_* / QUICKREPORT 快报设置）
db          SQLAlchemy 2.0 异步：ChatMessage（可读日志）+ ChatTurn（精确恢复）
entry       cli.py / web.py（SSE 流式 + /api/sessions + /api/quickreport/*（latest/generate/status/history/report/{day}）+ /api/skills* + /api/settings/mcp* + /api/rag/*）
mcp_gateway **独立进程/端口（:8766）**：唯一持有上游 MCP 源的服务——按源开关 + 动态聚合，对外说真 MCP 协议
            （config/sources/toggle_store/pool/mcp_endpoint/admin/app + providers/{multi_domain,ifind}；复用 providers.tools.{mcp,wind} 的连接实现）
external_sources 免费源 MCP server（AkShare / 财经新闻）：**独立进程 + 独立 requirements.txt**，不属于本包，网关按 URL 连它们
mcp_server  内置数据 server（另一个东西，:8765，默认停用仅后备；自建 streamable-http + 每接口一个工具）
scripts/web React + TS + Tailwind v4 + Vite + Zustand 前端（三栏工作台；`npm run build` → dist，由 web.py 挂载；Sidebar「聊天/快报/技能/设置」四视图切换；快报视图为 ECharts 仪表盘）
```

**进程拓扑（解耦形态）**：`浏览器 → demo(:8010, demomcp) → [MCP] → mcp-gateway(:8766) → [MCP] → Tushare 官方 MCP / 万得 7 域 / iFind 7 域 / AkShare / 财经新闻`。
demomcp 侧只有一条到网关的连接（保活+断线重建）；停掉网关 demo 照常活着（页面显示「网关不可达」、对话退化为纯 LLM），
停掉 demo 网关照常对外服务。故意**不设 depends_on**，靠 demomcp 的退避重连收敛。

**核心链路**：`Agent.run(history, on_text/on_thinking/on_tool/on_process)` → LangGraph 五节点 `router`（意图分类 + 越界判断）→ `rewrite_query`（RAG 查询改写）→ `tool_rag`（LLM 选工具 + **并行** `ToolProvider.call_tool` 与 RAG 检索——`RetrievalPlan` 检索进证据；`market` 意图跳过 RAG）→ `synthesizer`（整合 + 引用 + 免责声明）／越界或无证据（工具与 rag_chunks 双空）走 `fallback`。`tool_rag` 经**条件自环**支持**多轮取数**（agentic tool loop，`routes.make_route_after_tool_rag(max_iterations)`）：每轮执行后 LLM 看返回判定数据是否足够——足够就停止（返回无 `tool_calls`）去 `synthesizer`，仍缺就续调下一批工具；上限 `DEMO_MAX_ITERATIONS`（默认 10），`max_iterations=1` 即等价旧单轮行为。RAG **每轮只第一次检索**（`GraphState.rag_retrieved`）；`evidence/rag_chunks/tool_results` **跨轮累积**（LangGraph 以节点返回值整体替换这些键，`tool_rag` 必须读旧并回传全量）。**进展守卫**：某工具轮未新增任何证据/检索 → `no_progress_count` 递增，连续达 `no_progress_cap`（默认 2）即终止——防「无权限/空数据/查不到」时反复空转烧满 `max_iterations` 造成死循环（`agentic tool loop` 的已知坑）。循环可视化：节点每轮经 `on_process` 抛 `loop_turn{round,status,tools,evidence}`（status ∈ continue/stop/max-reached/no-progress），并给 `stage/plan/aggregate` 带 `round`，前端据此按轮渲染。节点内 `LLMClient.chat(stream…)`（DeepSeek 流式 + tool_calls + reasoning_content）透传回调；工具直接经 `MCPToolProvider.call_tool`（重试+超时）→ **MCP 网关**（`MCP_GATEWAY_URL`）→ 网关再分发到 Tushare 官方 MCP / 万得，可查任意 A 股/任意接口。**选工具那轮 `llm.chat(tools=…)` 只喂相关子集**（`graph.tool_select.select_tools` per-query：恒保留 `list_apis/get_api_info/query/stock_basic`，其余按金融词汇重叠打分取 top-K（`TOOL_MAX_REVEALED`），可用性 `catalog` 剔除 blocked/down；执行仍走全量 `tools.call_tool`，不受限）。结果归一化为 `AgentResult`；错误一律转 `is_error` ToolResult，图永不崩。

**延迟与取数完整性（2026-09-12，`fix(graph): agent 并行取数延迟优化 + 财务数据完整性与准确性修复`）**：
- **RAG 提前并行**：`_rag_retrieve` 由 `asyncio.create_task` 在**选工具的 LLM 调用之前**发起，与之重叠执行（原先是串行叠加两段耗时）；`rewrite_query` 按意图跳过不需要 RAG 的场景（`market` 等），省一次同步 LLM 往返。
- **单工具护栏超时** `DEMO_TOOL_CALL_TIMEOUT`（默认 90s，`_safe_call_tool`）：防一个慢/挂死的工具拖住整轮 `gather`，其它已就绪结果 + RAG 仍能按时汇总。90s 不是随便取的——`MCPToolProvider` 自身最坏情况（重试 2×30s + 退避 + 一次断线重连）已接近 90s，设太低会打断「第二次尝试即将成功」的可恢复慢调用；仍明显小于 `AGENT_IDLE_TIMEOUT(240s)`。
- **模型分工**：前端「深度思考」（`deepseek-reasoner`）**只作用于最终 `synthesizer`**（`Agent(..., synth_llm=…)` → `build_research_graph`）；`router`/`rewrite_query`/选工具三步改用快模型——这三步要么非流式不展示推理、要么是循环体，套深度思考纯拉长延迟无收益。改 `_FakeAgent` 签名的测试（`test_mcp_switch_web.py`/`test_web_tools_singleton.py`）就是为它。
- **`select_system` 拼接顺序 = 稳定前缀 + 动态尾部**：让 DeepSeek 服务端前缀缓存能在多轮取数循环之间命中；往前缀里塞每轮都变的东西会**静默**废掉缓存。
- **`REPORT_BASELINE_FAMILIES`（`tool_select.py`）**：利润表/资产负债表/现金流量表/财务指标/估值/业绩预告快报，在 `report`/`compare` 意图**或命中 skill** 时恒揭示、不占 `TOOL_MAX_REVEALED` 限额。修的是真实 bug——「写一份 XX 业绩点评报告」这类问法字面没命中关键词，模型连 `income`/`balancesheet` 都看不到。`tests/test_select_system_hint.py` 锁住提示词清单与该常量的同步性。
- **`curation_intent`（`nodes.py`）**：命中 skill 时按 `report` 力度处理取数与提示，不依赖 router 对裸词查询的粗粒度猜测（`forced_skill` 场景下 router 看不到技能清单，对「寒武纪」这类裸标的词几乎总猜成 `market`）。

**理由关键**：`Agent` 只依赖 `LLMClient` + `ToolProvider` 两协议；图在 `demomcp/graph` 内、由 `build_research_graph(llm, tools, tool_defs, …)` 闭包注入，节点经 `config.configurable` 读回调。消息帧（`assistant_message` / `tool_results_messages`）由 LLM 实现渲染 → 换后端不改图。`on_text`/`on_thinking` 在节点内流式吐出，`on_tool` 在 tool_rag 内逐次抛出（供前端做「思考轨迹」）。

**RAG（`demomcp/rag/`）**：针对 A 股年报 PDF 的问答型 RAG，**离线优先、独立于主链路**（不进 `entry→agents→interfaces`）。摄取：`pdf_parser`（PyMuPDF 解析 → 文本/图片/表格块）→ `section_tree`（字号聚类+编号正则检测章节、去目录页）→ `segments`（块归类、图片交 `captioner`、表格 `table_split` 行分块）→ `chunking`（句感知稠密分块 + 表格独立成块）→ `embedder`（`HashingEmbedder` 默认 / `ApiEmbedder` 走 OpenAI 兼容 `/embeddings`）+ `store`（默认 `InMemoryVectorStore`）& `bm25`（jieba 词元）。检索：`query_build` → `hybrid_retriever`（**三路 RRF**：节稠密+节 BM25+块稠密 → 按 `(doc,section)` 聚类 → top 节 → 重排池 → `reranker` → 阈值截断+`top_k`）→ `RagChunk`。引用：`citing` 确定性生成内联标记与参考列表（不编页码）。默认 `rag_use_real=False` → 纯 Python/确定性；真实后端需 `uv sync --extra rag-full` + `RAG_USE_REAL=true` + `RAG_EMBEDDING_API_KEY`。**已接入实时图**：`rag/runtime.build_runtime_retriever(config)` 懒加载 `hybrid_retriever.build_retriever`，`tool_rag` 按 `RetrievalPlan`（rewritten_query/concepts/filters/strategy，意图→strategy：report=factual/compare=structural）检索+重排进证据；`RAG_CORPUS_DIR` 可选启动时 ingest 建索引（无则检索为空、不崩）。`AgentResult.structured` 输出结构化 answer/claims/citations（`CiteRef` 元数据：公司/年份/页码/章节/内联）/metadata（`request` 归一化参数 + `validation.errors`）。`retriever.py` 的 `NullRetriever` 为遗留占位、无引用。文档：`docs/ARCHITECTURE.md`（现状系统架构含图）、`docs/RAG_INTEGRATION.md`（RAG 现状权威含全量配置表）、`docs/RAG_FINANCE.md`（设计蓝图）。取数面（曾为语义工具层 `StockToolProvider`，`stocks.py`）：已移除，现直接用原始 `MCPToolProvider`（`list_tools` 自动发现全部接口 + `query`），不再限定标的；`code!=0`（权限/积分不足）为业务结果 → 友好 `is_error=False` 让 LLM 转述，真异常才 `is_error=True`。

**报告 skill（`demomcp/graph/skills.py`）**：把「某类结构化投研报告」封装成可被 `router` 命中的 `Skill`（frozen dataclass）——`id/name/description`（router 依 description 匹配，命中后在 JSON 的 `skill` 字段回填 id）、`system_prompt(base)->str`（synthesizer 系统提示词）、`render(evidence, ctx)->str`（**确定性渲染器，非空则 synthesizer 完全不叫 LLM 写文案**）、`tool_hint`（追加进选工具的 system）、`tool_families`（恒保留的接口名子串）、`should_rag`/`strategy`（覆盖该用例的 RAG 取舍）。首个成员 `ai_supply_chain_tracker`（AI 算力产业链高频跟踪快报）走 `tracker_render.render_tracker`：容错 JSON 解析 + 字段别名匹配，把工具证据填进固定六段 Markdown，**匹配不到就标『数据未接入』，绝不编造**。加新**内置**报告类型 = 往 `BUILTIN_SKILLS` 里加一条 entry。**`SKILLS` = `BUILTIN_SKILLS`（1 条快报）+ claude-for 技能库（63 条，见下节）= 64 条**；`skills.py` 依赖 `prompts.ROUTER_BASE` + `tracker_render` + `skill_loader` + `config.skill_toggle`（不 import graph 内核，避免循环）。注意 `tool_rag` 会把证据 content 截断到 `_MAX_EVIDENCE_CHARS`（`_truncate_head_tail`：长内容保首尾、中段折叠一行省略标记——daily 类接口降序返回，首尾恰是区间两端；整段切头会让「全年涨跌幅」类计算缺基期值，线上事故 243 行 index_daily 截后只剩 2024-12 几行）；仍可能截在 JSON 中途 → 该段解析失败 → 标未接入（安全退化，不造假）。

**claude-for 技能库（`demomcp/skill_library/` + `graph/skill_loader.py` + `graph/skill_catalog.py`）**：把 claude-for 插件生态的 **63 个 `SKILL.md`** 反向导入本项目的 skill 注册表（方案与 13 项决策见 `docs/SKILLS_IMPORT_PLAN.md`，实测事实基线见 `docs/SKILLS_INTEGRATION_DECISIONS.md`）。语料 **vendored 进包内**（`skill_library/claude-for/vertical-plugins/<域>/skills/<技能>/SKILL.md`，upstream `59e97ee`），运行期不读参考仓库；`scripts/sync_claude_for_skills.py --src <path> [--dry-run]` 只在开发期刷新。装配 = `load_library()` → `LoadedSkill` → `skills.to_skill()`：
- **正文按小节拆两路**（`split_blocks` + `route_block`）：`Data Sources`/`Workflow`/`Key Terms` 等取数类 → `tool_hint`（tool_rag 节点）；`Purpose`/`Output`/`Format`/写作类 `Step N`（标题含 Draft/Report/Write/Commentary/Quality Check）/其余 → `system_prompt`（synthesizer）。**只有 14/63 有独立 Output 小节**，输出骨架多埋在「Step 6: Draft the Report」里，所以写作步骤必须归 synthesizer。
- **`system_prompt` 的拼接顺序即优先级**：`base` → **本项目输出纪律段**（`LIBRARY_DISCIPLINE`）→ 工具名对照段 → 能力降级段 → 技能输出侧正文。纪律段**不是可选文案**：`skill.system_prompt` 在 synthesizer 是**整体替换** `synth_system`（`nodes.py:658`），不包这一层就会丢掉三反编造纪律与「正文不写来源名」。
- **工具名对照**：正文的 `wind_*`/`ifind_*` 是 claude-for 环境的具名工具，本项目网关只有 3 个懒发现元工具 → 生成「旧名 → `wind_query(api_name=…)` / `ifind_query(query=…)`」；免费源 11 个工具名与 `external_sources/` **同名**（正文里出现最多的就是它们：`get_financials`×119、`get_quote`×62…），只附 Tushare 官方等价建议。
- **`tool_families` 与 `tool_mapping` 是两件事，别合并**：mapping 只翻译**真实存在的小写工具名**；families 由 `detect_source_families()` 按「正文走哪家源」（散文里的 `万得/Wind`、`同花顺/iFind` 也算）判断。理由：`ifind_list_apis/get_api_info/query` **不在** `META_TOOL_NAMES` 里（只有 Wind 三件套在），不靠 families 恒保留，iFind 这一路压根进不了本轮工具面——只看工具名的话 63 篇里仅 6 篇能用 iFind。
- **`tool_hint` 硬上限 3000 字符**（`_join_capped` 按块边界截断）：tool_rag 是多轮循环节点，**每轮都带这段**。
- **能力降级是纯规则**（正文命中 `xlsx|pptx|Excel|…` ≥3 次 → 追加 `CAPABILITY_NOTE`，实测命中 7 条），不维护手工名单，防随 upstream 漂移。
- **`should_rag` 只开 16 条**（`RAG_SKILL_IDS`，财报/估值类，`strategy=factual`），其余 False。
- **router 清单必须用中文短描述**：63 条原始 `description` 合计 **20.7KB**，而 router 是**每次提问都跑**的单次分类调用 → `Skill.catalog_line` 取自 `skill_catalog.CATALOG`（人工维护的「中文短名 + ≤40 字」），整张清单 **≈4.4KB**。缺条目 → WARNING + 回落 description 首句（清单立刻变长且中英混杂），`tests/test_skill_loader.py` 守住表的完整性与 6KB 上限。
- **5 对跨域撞名不是重复文件**：`china-{accrual-schedule,break-trace,gl-recon,roll-forward,variance-commentary}` 在 `china-finance`（上市公司口径）与 `fund-admin`（基金组合口径）下**各有一份、Workflow 完全不同** → 两份都导入，`fund-admin` 那 5 条 id 加 `-fund` 后缀（`ID_SUFFIX_BY_DOMAIN`）。
- **`WIND_API_KEY` 这类环境变量不是工具名**：工具名扫描**区分大小写**（工具名一律小写、环境变量一律大写）+ `_NOT_TOOLS` 兜底小写写法 + 排掉结尾 `_` 的通配写法。不做这层会翻出 `wind_query(api_name="api_key")` 之类**根本不存在的接口**喂给 LLM（2026-09-08 真实链路踩到）。
- **按技能开关**：`config/skill_toggle.py`（`data/settings/skills.json`，镜像 `mcp_toggle.py`）；生效点是 `build_router_system` **每次 router 现读** → 改开关无需重启。停用的技能不进 router 清单，也不能被「快速使用」强制指定（`web.py` 忽略并 WARNING）。
- **「快速使用」= `forced_skill`**：`ChatRequest.skill` → `Agent.run(forced_skill=…)` → `GraphState.forced_skill` → `make_router` **不拼技能清单**（省 ~4.4KB/次）、只判 intent + 越界，skill 用指定值（LLM 这轮没看到清单，它填的 skill 不可信）。与 `mode="quick"` 冲突时**按 agent 跑**（quick 传 `skills=[]` 会让指定技能静默失效）。
- 逃生开关 `SKILL_LIBRARY_ENABLED=false` → `SKILLS` 只剩内置 1 条（`SKILL_LIBRARY_DIR` 可换语料目录）。

**Web**：`POST /chat` 走 SSE（`thinking/text/tool_call/tool_result/process/done/error`（+末尾 `__end__` 哨兵），`process.kind` ∈ intent/rewrite/stage/retrieval/funnel/plan/params/validation/aggregate，`tool_call{name,input}` → `tool_result{content,ok}`，前端据此渲染默认展开的工具卡），`ChatRequest` 支持可选 `model`（前端「深度思考」开关传 `deepseek-reasoner`，从而真正流出 `thinking`/reasoning_content）；`lifespan` 复用 `store`；`GET|DELETE /api/sessions`、`GET /api/sessions/{id}`；**恢复**用 `ChatTurn.messages_json`（每轮完整 OpenAI 消息，累计式）精确还原 tool_calls/tool_call_id；每轮 UI payload（answer/sources/citations/claims/metadata 等）落 `ChatTurnData`，经 `GET /api/sessions/{id}/turns` 回传。另挂 `POST /api/rag/retrieve` 与 `GET /api/rag/health`（`rag/server.py` 纯函数，lazy 构建 retriever，异常→degraded）。

**快报（`demomcp/quickreport/`）**：独立于 Agent/LLM 主链路的**确定性**六段式日度报告（板块概览/标的池行情速览/关键公告/业绩预告异动(阈值 >50 或 <-20，watchlist.json 可配)/产业链催化事件/一句话研判≤150字）。管线 = `server.generate_from`（web 端点/定时任务/独立脚本共用一个入口）→ `pipeline.build_report`（五段 `asyncio.gather` 并行 + 段内降级链 + Semaphore(4) 限流 + 段级 `wait_for(60s)`）→ `projection`（纯计算，import `tracker_render` 的无状态纯函数与别名表——**该文件本体零改动**，其 Markdown 渲染被测试锁定）→ `store.save_report`（`data/quickreport/latest.json` + 按日存档 `history/{YYYY-MM-DD}.json`，镜像 curate.save_catalog）。**不经 LLM**：`call_tool` 直取 → 阈值/拼接计算 → 前端渲染。**JSON 契约**：段 `status ∈ ok|empty|na`（na=数据未接入/empty=本期无）；数值一律「数值+文本」双份（`pct`+`pct_text`）；顶层 `missing[]/errors[]`。**接口真相**：公告用官方名 **`anns_d`**（`announcement` 等是 LLM 选工具的近似子串，别当真名 call）；预告同比=forecast 的 `p_change_min/max`、express 的 `yoy_net_profit`（`tracker_render._FOR_YOY` 别名缺这两个，predicate.py 已补）；行情用 **`daily(trade_date=…)`/`daily_basic(trade_date=…)` 全市场快照**按池滤（不逐标的逗号串，官方不支持）；新闻 `news(src=…)`（单独权限，大概率 blocked）→ Wind `wind_get_financial_news` 兜底 → na。标的池=`data/quickreport/watchlist.json`（211 只，`scripts/build_quickreport_watchlist.py` 由 CSV+模板池生成，提交）。**前端（2026-09-10 起为 ECharts 仪表盘）**：Sidebar「聊天/快报/技能/设置」四视图切换（App.tsx `view` state，收起态为图标按钮）；快报视图 = 头部条（`ReportHeaderBar`）+ KPI 指标行（`ReportKpiRow`/`KpiTile`）+ 左右分栏，一段一卡在 `components/report/panels/`（Board/IndexTrend/Watchlist/PoolFlow/Announce/Forecast/News/Brief/DataSource），图表 option 构造器在 `components/report/charts/`，表格仍走 `ReportTable`（支持 React 节点单元格，`ui/DataTable` 只支持纯文本），类型+容错解析在 `lib/quickReport.ts`（拒绝 `lib/types.ts`），新鲜度/状态计算在 `lib/quickReportStatus.ts` + `lib/reportMetrics.ts`。**图表只装 `echarts@5.6.0` 一个依赖**（不装 `echarts-for-react`，也不装 `@types/echarts`——那是过期的 DefinitelyTyped 桩，echarts 自带类型）；`components/charts/echarts-setup.ts` 是**全站唯一注册点**，只 `use()` 用到的组件（Line/Bar/Grid/Tooltip/Legend/DataZoomInside/MarkLine/Canvas），`EChart.tsx` 是唯一的 React 包装。
**可观测端点**：`GET /api/quickreport/status` 是**纯读**的（latest.json / last_error.json / watchlist.json + 本地时钟），**不触发任何 MCP 连接**，前端可秒级轮询——返回新鲜度、各段来源、`server_time`（**必须给**：「距下次生成还有多久」要用 `next_run_at - server_time` 再套客户端时钟，否则机器时钟一歪这个看板就在说谎）、`missing_required`、`last_error`。时间戳**一律带 UTC 偏移**（前端 `formatTime` 见到无偏移串会补 `Z` 当 UTC 解析，一个裸北京墙钟串会被显示成早 8 小时）。另有 `GET /api/quickreport/history`（按日存档摘要）与 `GET /api/quickreport/report/{YYYY-MM-DD}`。
**必需段守卫**：`config.DEFAULT_REQUIRED_SECTIONS` + `server.required_missing`——**缺必需段的报告不算最新**，调度器下次触发（含进程重启后的补跑）会重试，而不是整天停在部分降级态。定时与手动生成**共用一把锁**（`app.state.quickreport_lock`）。
**来源标注（provenance）**：`quickreport/source.py` 按**工具名**分类 `tushare|wind|ifind|free|pipeline`（网关对快报是不透明端点，`call_tool` 只有工具名没有 source_id），常量取自 `interfaces/types.py` 全仓唯一一份；刻意**不 import `demomcp.agents.*`**（会拉进 LangGraph，破坏「与主链路解耦」的承诺）。它同时是快报侧判「这条返回算不算业务失败」的依据——五个源的成功约定互相矛盾，见下方 `gateway_business_error` 一条。

## 关键坑（改了会踩）

- **`mcp` 钉 `>=1.28,<2`**（FastMCP）；`ClientSession.call_tool` 的 `read_timeout_seconds` 参数是 **`timedelta`，不是秒**（传 `timedelta(seconds=…)`）。
- **唯一取数入口 = MCP 网关**：`entry/web.py::_tools_context` / `entry/cli.py` 只经 `settings.mcp_gateway_url` 建一条 `MCPToolProvider` 连接（保活+断线重建照旧复用）。**demomcp 侧已删除** `tushare_mcp_url`/`wind_api_key`/`wind_enabled`/`wind_configured`/`effective_system_prompt` 与 `wind.agent_tool_provider`——多源装配是 `mcp_gateway/{sources,pool}.py` 的职责。想改「连哪些源」去改网关，别在 demomcp 里找。
  - 各源用法提示词（`WIND_USAGE_GUIDE` / `IFIND_USAGE_GUIDE` / `FREE_SOURCE_USAGE_GUIDE`）不再看配置，改由 `agents/agent.py::base_system_for(cfg, tool_defs)` 按「本轮工具清单里有没有它的工具」逐段注入（Wind/iFind 看前缀，免费源看哨兵名）→ 网关关掉某源，下次建图自动不提它（两边永不打架）。**这几段不是可有可无的文案**：四家参数风格互不兼容（Tushare 结构化字段+带后缀代码 / Wind 自然语言+Wind 后缀 / iFind 单个自然语言 query / 免费源 6 位裸代码），漏注入就会拿另一家的习惯传参而**静默**取不到数，`tests/test_source_usage_guides.py` 锁住每一处注入与几条关键契约。
  - 网关自身：`mcp_gateway/mcp_endpoint.py` 用**低层** `mcp.server.lowlevel.Server` + `StreamableHTTPSessionManager`（`/mcp`），`list_tools`/`call_tool` 是每次现查「当前启用的源」的 handler → 开关改动无需重启网关即生效；`call_tool` 必须返回 `types.CallToolResult(isError=…)`（返回裸 list 会被 SDK 恒置 `isError=False`，业务错就丢了）。`pool.py` 是 `entry/web.py` 那套池生命周期（懒建/热启动退避/周期重建/退休等在途归零）按 source_id 泛化的一份。
  - 配置没配好**不崩进程**（`restart: unless-stopped` 下会变重启风暴）：`GatewaySettings.config_problems()` → 启动 ERROR 日志 + `/admin/health` 的 `config_problems` + 前端「设置」页显示。
- `config/env.py`：`PROJECT_ROOT = parents[2]`。
- **`create_all` 不会 ALTER 既有表**：改了 `models.py` 后要删掉旧的 `demo.db`（schema 漂移会 500）。
- **SSE 用 POST + `ReadableStream`** 解析，不是 `EventSource`（无法 POST body）。前端 `react-markdown`+`remark-gfm`+`remark-breaks` 渲染（默认不解析原始 HTML，天然防 XSS，无需 DOMPurify）。
- **错误永不崩图**：图内节点对 `llm.chat` 包 `except Exception` 转 `fallback`；`Agent.run` 再 `except Exception`（含 `ExceptionGroup`）转 `stopped_reason="error"`。二者都**不**捕 `BaseException`（`asyncio.CancelledError`，客户端断连/`task.cancel()` 照常透传）→ 避免 LangGraph 把节点异常汇成 `ExceptionGroup: unhandled errors in a TaskGroup` 上抛。
- **工具重试语义（易记反）**：工具返回 `{code,msg,row_count,data}`；`MCPToolProvider._call_once` **对『抛出的异常』和『业务失败（`isError` 或 `code!=0`）』都重试** `DEMO_MCP_RETRIES` 次（默认 2）。重试耗尽后分三路：权限类失败（`msg` 含 积分/权限/无权限/提升）→ 友好提示 + `is_error=False`（让 LLM 如实转述并自行改道）；其它业务失败 → 原文 + 沿用 `result.isError`；异常耗尽 → `"Error calling X after N tries"` + `is_error=True`。
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
- **多期财务数据必须表格化，不能按字符位置截断**（2026-09-12，真实事故）：官方财报接口返回的是按 `end_date` 排列的数组。旧的 `_truncate_head_tail` 按**字符位置**保首尾、折叠中段，会把用户实际问到的报告期折进匿名的「中间省略」提示里——模型收到「数据确实取到、只是没给你看」的暗示后，就用视野内邻近可见期的数值顶替、换个标签充数（**真实复现并经 Wind 交叉核对**：宁德时代 2025H1/2025Q1 被填成了 2024H1/2024Q1 的真实数值）。现在 `_evidence_content` 是统一入口：命中带 `end_date` 的报告期数组 → 走 `_period_label`（YYYYMMDD → Q1/H1/前三季度/年报）+ `_dedupe_period_rows`（按 `end_date+report_type` 去重，含官方接口偶发的完全重复行）+ `_render_period_table`（确定性 Markdown 表格，**按整期截断而非字符位置**），每期数值紧邻自己的标签；其余场景保持 `_truncate_head_tail` 行为逐字节不变（它本身也已改成按 **JSON 行边界**截断，保证任意一行要么完整保留、要么完整落入省略段，不再切穿单条记录）。`synth_system` 配套加了「多期数据认标签，不认位置」。改这块前先跑 `tests/test_period_table.py`（含真实抓取的宁德时代 `fina_indicator`/`income` fixture）。
- **别把「取不到数」的具体措辞硬塞给选工具轮**：`select_system` 里曾硬编码『数据未接入』字样，导致模型把整张表填满占位行（真实案例：整个「数据质量检查」表全是占位）。现在改为「取不到就如实放弃，呈现方式交给收尾阶段」，`synth_system` 与 `LIBRARY_DISCIPLINE` 则补了「整表都是占位符就整节删掉」的反模式提醒。
- **证据条目有两份内容，别混用**：`tool_rag` 给每条 tool 证据同时写 `content`（`_truncate_head_tail` 截到 `_MAX_EVIDENCE_CHARS=2000`，**喂 LLM**，`_evidence_digest` 只读它）和 `raw`（截到 `_MAX_SOURCE_RAW=20000`，**只走 `_structured` → `structured.sources[].data`**，供前端渲染表格）。
- **零证据停止轮的「直接作答」通道（`direct_answer`）**：`tool_rag` 停止轮（无 tool_calls）且总证据/rag 双空时，若 `resp.text` 过 `_accept_as_direct_answer`（非空、无拒答措辞——**长度无关**因该路径无重试、≥30 字符、不含「取不到/没找到/无权限/未接入」等失败措辞）→ 存 `direct_answer`、`fallback_reason=None`，`routes` 定向 `synthesizer`——synthesizer 顶部短路：不调 LLM、**不接 on_text**（text 已在选工具轮走 `on_thinking`，答案区由 done 的 `structured.answer` 填充）、不动 messages（assistant 帧已 append）、`ended_reason=end_turn`、`metadata.direct_answer=True`。背景：同题二答时 LLM 会回显上一轮答案（历史是累计式的，见 f2d8b91b 事故），旧逻辑当「没取到数」误导兜底。`no_progress` 兜底文案相应改为「本轮未调用取数工具且没有获取到可校验的数据…」；改判定条件（如加 `and not tool_results`）前先看 `tests/test_agent_loop.py` 的 `test_no_tool_direct_answer_*` 家族。往 `raw` 里塞东西不会涨 token，但把 `raw` 写进 digest 会；反过来，把 `sources[].data` 改回读 `content` 会让前端表格只剩 2000 字的半截 JSON（解析失败→退化成纯文本）。前端 `scripts/web/src/lib/table.ts` 的 `parseToolTable` 是**全站唯一**的表格解析器（引用来源卡与思考过程工具卡共用），认 4 种形态：裸数组 / `{data:[…]}`（含代理 `{code,msg,row_count,data}`）/ `{data:{columns,rows}}` / 裸对象；`{data:{items:[{content}]}}`、权限提示纯文本、`"[]"`、截断导致的解析失败一律返回 `null` → 调用方回退 `<pre>` 原文（安全退化，绝不空白）。
  **synthesizer 空文本/截断与拒答同路处理**：`_is_degenerate_answer(answer) or resp.stop_reason == "max_tokens"` 触发一次重试（预算 `max(max_tokens*2, 16384)`——2026-09-08 事故：v4-flash 的思考计入 max_tokens，8192 预算被 reasoning 吃光（实测 reasoning_tokens=8190）→ content 为空 → 旧逻辑只回一句「已取到数据，但未能生成总结。」且不重试），重试仍失败 → 诚实兜底「已取到 N 条数据…」（不标万得来源）。**assistant 帧 content 与 tool_calls 双空不得写入消息历史**：`nodes.py::_append_assistant_frame` 拦源头，DeepSeek API 对这类帧下一轮直接 400（Invalid assistant message: content or tool_calls must be set，整个会话报废）；`db/store.py::_sanitize_turn_messages` 在恢复时清洗已污染的历史。

- **每轮 UI 载荷带 `created_at`**：`web.py` 往 `ChatTurnData` 的 blob 里写 `datetime.now(UTC).isoformat()`（blob 是自由 JSON，**不触发 schema 漂移、不用删 `demo.db`**），前端来源卡的日期行取它；直播轮次 SSE 不带时间戳，由 `useChatStore.send` 前端打点。历史轮次没有该字段 → 日期行自动隐藏。
- **取数面全开**：不再有语义/白名单层（`stocks.py`/`StockToolProvider` 已删）；直接用 `MCPToolProvider`，可查任意 A 股 / 任意接口。
- **RAG 只在解析出语料公司时检索**：`_rag_retrieve` 在 `infer_filters(q).company is None`（非比亚迪/宁德时代/泛行业）时**直接空返**，避免无公司过滤时把别家年报切片搜出来污染答案（工具路兜底）。口语别名表下沉到 `rag/query_build.DOMAIN_ALIASES`（`nodes._domain_expand` 与 `tool_select._domain_terms` 共用，避免漂移）。
- **skill 所需接口恒保留**：`Skill.tool_families`（接口名子串）经 `curate(... skill_tools=...)` 传给 `select_tools`，这些接口**不进 `TOOL_MAX_REVEALED` 限额**（如 `ai_supply_chain_tracker` 保留 daily/daily_basic/forecast/announcement/moneyflow/top_list/…）。
- **超时是一条有序阶梯，改一层要连着改**（`fix: 远端库/LLM 超时收紧 + SSE 看门狗`，防「前端无限转圈」）：
  `deepseek.py` 的 `_LLM_TIMEOUT`（connect 10s / **read 180s**）且 `_LLM_MAX_RETRIES = 0`（SDK 默认读超时 600s、自带重试 2 次会把静默叠到 ~9min）
  < `web.py` 的 `AGENT_IDLE_TIMEOUT = 240.0`（`queue.get` 的**完全静默**上限，到点 `task.cancel()` + `_settle` 5s 收尾 + 发 `error` 与 `__end__`，保证 SSE 一定收敛）。
  **`AGENT_IDLE_TIMEOUT` 必须大于单次 LLM 最长静默**，否则会误杀 `deepseek-reasoner` 的正常长思考（非流式节点可能长时间无事件）。
  `db/store.build_store` 同向收口：`pool_pre_ping` + `pool_recycle=1800` + `pool_timeout=10`，Postgres 再补 `connect_args={"command_timeout": 30}`（**只对 asyncpg 生效**，SQLite 不能传）。
- **LLM 相对时间表达必须以真实日期为锚**：`prompts.today_context()` 把「今天=YYYY-MM-DD（北京时间，走 `config.cn_tz()`）」注入**全部 LLM 节点**的 system prompt（`select_system` 每个意图都带），否则模型按训练时间感猜「最近30日/今天/最新」，会把「最新数据」算到半年/数年前。`tests/test_prompts_today_context.py` 守住每一处注入点。**引用/断言「今天」一律相对 `datetime.now(cn_tz()).date()` 计算**——硬编码具体日历日（如 20260904）会在日期漂过后挂掉（`test_resolve_report_date_trade_cal_and_fallback` 曾因此失败）。
- **快报（quickreport）关键坑**：
  - **时区**：调度全程 `ZoneInfo("Asia/Shanghai")` 计算后转 UTC sleep——容器常跑 UTC，直接 `datetime.now()` 会让 08:30 漂到 00:30。**Windows 开发机无系统 tz 数据库**（`ZoneInfo("Asia/Shanghai")` 直接抛）→ 统一走 `config.cn_tz()`（失败回退固定 UTC+8，该时区无夏令时等价）；**不要**在 quickreport 里直接用 `ZoneInfo(...)`。
  - **接口名**：公告用 `anns_d`；`announcement`/`concept_cons`/`sector` 是 LLM 选工具的近似子串，当真名 call 会 `Unknown tool`。预告同比字段 `p_change_min/max`（forecast）与 `yoy_net_profit`（express）不在 `tracker_render._FOR_YOY` 里——`predicate.yoy_of` 已补，改 alias 表时两处都要同步。
  - **官方 MCP 真实权限矩阵（2026-09-05 实测；快报数据源按此落地，agent 模式仅受影响接口的子集提示）**：
    - ✅ 有权限：`daily`/`daily_basic`/`moneyflow`/`trade_cal`、核心指数 `index_basic`(SSE+SZ)→`index_daily`（**板块段主源**，申万行业指数 `index_daily(801010.SI)` 返回空、`sw_daily` 40203）、类型化公告 `stk_holdertrade`/`repurchase`/`block_trade`、`forecast`/`express`。
    - ❌ 40203 无权限：`anns_d`（**全形态**——公告段已弃用，改类型化组合源）、`news`/`cctv_news`、`ths_index`/`ths_daily`/`index_classify`/`sw_daily`、`moneyflow_ind_*` 板块资金流（配置 `dc_flow=false` 跳过）。
    - ⚠️ **mcp.py 把权限失败归一为一句话文本**（`调用 news 失败：该接口需更高积分或当前账号无权限（代理提示：…）`，`is_error=False`）——非 JSON，`pipeline._biz_fail_reason` 必须识别该特征（以"调用"开头+「失败」/「Error calling」/「抱歉，您没有接口」），否则被当成「返回空」误标 empty 而非 na。
    - 参数坑：`anns_d.fields` 数组（已弃用）；`forecast`/`express` **无 start/end_date**（[50101]），用 `ann_date`（当日快照）或 `ts_code`（逗号批 ≤30/批）；`news` 日期为 `'YYYY-MM-DD HH:MM:SS'`；`wind_query(api_name="get_financial_news", params={"query":…})` 是新闻兜底（**M2 后具体 Wind 工具必须经 wind_query**，裸名 `wind_get_financial_news` 会 Unknown；返回 `{data:{items:[{content,pub_time}]}}` → `_unpack_wind_news` 专用解包）。
    - 单位：`moneyflow.net_mf_amount`/`net_amount`（万元 → 亿 ÷1e4，勿用 tracker_render._yi_num 启发式）；同比名字段可能是绝对值（`predicate.yoy_of` ±500% 守卫）。官方 MCP **无** `list_apis`/`get_api_info`/`query` meta 三件套（属内置 mcp_server）。
  - **行情走全市场快照**：`daily(trade_date=…)`/`daily_basic(trade_date=…)`/`moneyflow(trade_date=…)` 每次一张全市场快照按池滤；**不要**逐标的逗号串（官方 HTTP API 不支持，必失败）。
  - **业务失败一律当失败**：Tushare 信封 `code!=0`（含权限/积分/参数错）在 `pipeline._call` 统一返 None + errors 记录并走降级链——`data` 在 code!=0 时无意义，透传只会污染下游。
  - **status 三态语义**：`ok`（有行）/`empty`（链通但本期无，如非交易日）/`na`（未接入/全败）；`chain_ok` 只由**数据接口**（ths_daily/daily/anns_d 等）成功置位，分类接口（ths_index/index_classify）成功只算链路通、不算有数据。
  - `QUICKREPORT_AUTO=false`（测试）不建 lifespan 每日任务；手动端点 422 于 `watchlist.json` 缺失时**先于取工具池**返回（否则离线环境触发真实 MCP 连接）。
- **技能库的两处易错**（`graph/skill_loader.py`，详见上方专节）：①`tool_mapping`（只翻真实小写工具名）与 `tool_families`（按正文走哪家源，散文也算）**是两件事**——合并会让 iFind 整条路进不了工具面（`ifind_*` 元工具不在 `META_TOOL_NAMES` 里）；②工具名扫描**必须区分大小写**，否则 `WIND_API_KEY` 会被翻成 `wind_query(api_name="api_key")` 这种不存在的接口。改 `skill_catalog.py`/语料后**必跑** `tests/test_skill_loader.py`。
- **`ChatRequest` 新增 `skill` 字段（技能页「快速使用」）**：未知/停用 id **只忽略不报错**（前端可能拿着过期 id），但会打 WARNING；`forced_skill` 存在时**忽略 `mode="quick"`**（quick 传 `skills=[]`，指定技能会静默失效）。
- **`pyrightconfig.json` 的 `venvPath: ".."` 已过时**（仓库从子目录挪成根目录后没跟着改）：直接跑 `pyright` 会去仓库**上一级**找 `.venv`、解析不到任何依赖，凭空多报约 31 个 `reportMissingImports`。跑检查请加 `--venvpath .`。
  注意 pyright **当前并非零错误**（`--venvpath .` 下约 46 个，集中在 `tests/test_agent_loop.py`、`demomcp/rag/{store,embedder,persist,...}`、`graph/nodes.py`）——**不是**可用的提交门禁；真门禁是 `ruff`（干净）+ `pytest`（500 passed / 3 skipped）。
  其中 rag 相关的一部分是**没装 `rag-full` extra**（`pymilvus`/`pymupdf`/`jieba`/`rank-bm25` 在当前 `.venv` 里缺失）导致的解析失败，属预期——重依赖本就是函数体内懒加载。
- **`demomcp/agents/registry.py` 是废弃占位**：它里面那个 `CompositeToolProvider` **无任何引用**。`demomcp/providers/tools/composite.py` 里那份是通用工具（现仅 `tests/test_tool_provider.py` 引用）——网关的跨源合并走的是 `mcp_gateway/pool.py::GatewayToolProvider`（它要按「当前启用的源」动态重算，不能用 composite 的一次性 `_specs` 缓存）。改多源合并逻辑去 `mcp_gateway/pool.py`。
- **万得（Wind）源现在挂在网关上**：`mcp_gateway/.env` 的 `WIND_API_KEY`（Bearer 头）+ `WIND_ENABLED` 决定网关是否注册 `wind` 源（未配就不注册，`/admin/sources` 里也不出现，不留永远连不上的开关项）。`WindToolProvider`（`demomcp/providers/tools/wind.py`，网关复用）连 7 个域、内部编目 35 个具体接口但**对外只暴露 3 个懒发现元工具**（`wind_list_apis`/`wind_get_api_info`/`wind_query`），统一 `wind_` 前缀与 Tushare 原生名区分；单域连接失败跳过 + 60s 冷却重试。万得返回契约无 Tushare 的 `{code,msg,...}`，无 `code` 字段即视为成功。
- **同花顺 iFind 源（2026-09-08 接入并实测 7/7 域连通）**：与万得**同构**——7 个远程 streamable-http MCP 域、一个 key、`ifind_` 前缀 + 3 个懒发现元工具（内部目录实测 32 个接口）。四个必踩的差异：
  - ①**鉴权头是裸 token**（`Authorization: <token>`，**没有** `Bearer ` 前缀；抄万得会全域 401）。标准 TLS 校验即可连通——参考实现里的 `verify=False` + `check_hostname=False` 是多余的，别跟着抄。
  - ②URL 里 `global_stock` 域写作连字符 `hexin-ifind-ds-global-stock-mcp`（域名是下划线，唯一一处不一致，写错 404）。
  - ③**并发受套餐硬限**（免费 2 / 个人 5 / 企业 10，`IFIND_CONCURRENCY` 默认取最保守的 2，超限远端直接拒）。
  - ④**成功信封是 `code:1`，与 Tushare 的 `code:0` 正好相反** → 必须给 `mcp_tool_provider(business_error=ifind_business_error)`，否则 `mcp.py` 默认判据（`code != 0` 即业务失败）会把**每一次成功调用都当失败重试一遍**：实测 A/B 为 2 次远端往返 / 2.08s vs 修复后 1 次 / 0.87s，还白烧掉一半并发配额。`business_error` 是 2026-09-08 为此加进 `MCPToolProvider` 的可选判据参数，缺省行为逐字节不变。
  - **两条返回语义别搞反**：**查不到数据仍是 `code:1/success`**，提示语在 `data.answer` 里（正常返回，要让 LLM 如实转述，不能翻成错误）；而参数缺失/类型错这类硬错误远端会**挂住直到超时**、不返回错误信封（由 `mcp.py` 的超时+重试兜成 `is_error=True`）。`{"error": …}` 那个形状是参考实现自己代理层加的，iFind 远端从不返回。
  - iFind 接口**只吃一个自然语言 `query` 串**（`"科大讯飞2025年三季度的ROE"`），不是结构化字段——`IFIND_USAGE_GUIDE` 明确否掉 `ts_code/start_date`，删了这段 LLM 会按 Tushare 习惯传参而稳定取不到数。`data` 是**双重编码的 JSON 串**（里层 `{"answer": "<markdown 表格>"}`），前端 `parseToolTable` 认不出 → 安全退化成 `<pre>` 原文，不是 bug。
  - **刻意不硬编码接口清单**：参考实现把 31 个接口写死成 wrapper，已与远端漂移（暴露了已下线的 `search_funds`/`search_edb`/`search_global_stocks`/`search_trending_news`，又缺了新增的 `get_stock_performance` 与四个 `*_highfreq_quotes`）。懒发现自动跟着远端走。
- **免费源走「独立进程 + HTTP」（`external_sources/`）**：AkShare（行情/财报/行业/指数/基金 9 个工具）与财经新闻（个股新闻/市场头条 2 个）是 `import akshare` 的**本地库型** FastMCP server，不是远程 MCP。它们**自带 `requirements.txt`、不进主仓库 `pyproject.toml`**（akshare 会拖进 pandas/lxml 一大串），各自 `python external_sources/akshare_server.py --port 8000` 独立启停；网关只经 URL 当普通 MCP 客户端连它们，**与连 Tushare 官方 MCP 是同一条代码路径，没有特殊分支**。网关不负责拉起它们（没起来就是该源连不上，网关与 demomcp 照常工作）。两个坑：①它们的工具名**没有前缀**，`base_system_for` 只能按哨兵名（`get_market_overview`/`get_market_headlines`/`search_stock`）判断在不在本轮清单；②**代码格式是 6 位裸代码**（`600519`，不带 `.SH`），传后缀会 not found，靠 `FREE_SOURCE_USAGE_GUIDE` 讲清。它们默认传输改成了 **streamable-http**（参考项目原版只有 stdio|sse，而本项目客户端只实现 streamable-http）。定位是兜底与交叉验证，尤其补上 Tushare 官方 `news`/`cctv_news` 的 40203 空洞。**东财行情 CDN 对 AkShare 有 UA/TLS 指纹/IP 级三层反爬**（`akshare_server.py` 已打 UA + curl.exe 透明代理两层补丁，IP 封锁属环境、补不了），行情类工具在封禁窗口内会连续 `{"error": ...}`——那是业务结果不是 bug，细节见 `external_sources/README.md` 已知坑。
- **跨源撞名 = 先声明的源赢 + 告警**：`pool.list_tools` 对同名工具只保留先声明那条（`build_sources()` 的顺序）并丢掉重复的，同时打 WARNING。两件事都必要——同名不去重会让 `call_tool` 的路由随写入顺序漂，且重复的 tool 定义塞进 `llm.chat(tools=…)` 是非法载荷。目前 5 个源恰好不撞名（`wind_`/`ifind_` 带前缀，Tushare 与免费源用各自原生名）；**看到这条告警就说明某源改了命名，去 `sources.py` 给它加前缀，别指望 pool 兜住**。
- 实测工具面：Tushare 247 + Wind 3 + iFind 3（元工具，内部编目 32）+ AkShare 9 + 新闻 2。
- **`MCPToolProvider` 的 `business_error` 参数（2026-09-08）**：覆盖「怎样算业务失败」的判据，缺省 `_is_business_error`（Tushare 约定 `code != 0`）。加它的原因见上面 iFind 的 ④。**接新数据源时先确认它的成功码**——沿用默认判据会静默地把每次成功都重试一遍（不报错、只是慢一倍且烧配额，很难发现）。
- **`gateway_business_error`：demomcp → 网关**那一跳必须单独给判据（2026-09-08，`fix(mcp): 网关聚合连接用聚合判据`）。网关侧给 iFind 源传 `business_error` 只管**网关到 iFind** 那一跳；demomcp 到网关是**另一条连接、另一个 `MCPToolProvider`**，而这条连接上流着五个源的混合信封——Tushare `code:0` 成功、**iFind `code:1` 成功**，默认判据会把每一次成功的 iFind 调用判失败并重试。实测同一次调用（输出逐字节相同 len=886）：默认判据 1.97s → 聚合判据 0.82s（另一次测得 6.20s → 0.65s，倍数随远端时延波动），还双倍烧掉 iFind「套餐硬限并发 2」的配额。判据 = JSON dict 且 `code` **不属于 `{0, 1}`** 才算失败（非数字 `code`、bool 都不算——业务数据里可能恰好有个叫 code 的列）。**三处网关客户端构造点全部要传**：`entry/web.py` / `entry/cli.py` / `scripts/generate_quickreport.py`，所以受益的不只是快报，主对话链路一起变快。刻意**不改** `_is_business_error`（`tests/test_mcp_retry.py` 用 `{"code":1,"msg":"参数缺失"}` 当业务失败的标准样例锁定它，在 Tushare 语义下那是对的）——同一段文本的含义取决于它来自哪个源，这里是「多一个判据」不是「改判据」。**接新源若成功码不是 0/1，记得加进白名单。**
- **MCP 会话三件套：热启动 / 防断联保活 / 断线自动重建**（`_hot_start_tools` + `MCPToolProvider` owned 模式）：
  web 加载（lifespan）即由后台任务 `_hot_start_tools` 建工具池并 `warm_up`（Tushare 清单 + Wind 7 域），失败指数退避 5s→60s 重试直到成功——远端启动时不可用也能无流量自愈；`_get_tools` 懒路径保留（同一把双检锁，绝不重复建连）。`TOOL_POOL_HOT_START=false` 回归冷启动；`test_rag_server.py` 用该 env 保持离线确定（TestClient 会触发真 lifespan）。
  `MCPToolProvider` 带 url/`session_factory` 建 = owned 模式：`_keepalive_loop` 每 `DEMO_MCP_KEEPALIVE`（默认 45s，0=关）`send_ping()`；`_is_transport_failure`（httpx.*/OSError/超时/`McpError` -32000|408）→ 作废会话并重建（`_reconnect_lock` 单飞；除 -32601 等协议级错误——服务器活着只是没实现 ping，防重启风暴）。`call_tool` 内传输层异常先 `_invalidate` 再重试（一次失败恰好重建一次），业务 `code!=0` 永不重建；legacy 注入 session 的路径（测试用）行为逐字节不变，`close()` 绝不关它。`_recycle_tools_loop` 30min 整池重建保留为最后安全网；旧池经 `provider.close()` 关栈时停保活，无孤儿任务。

## 关键文件

`demomcp/graph/{builder,nodes,routes,prompts,state,tool_select}.py`（LangGraph 五节点 + 动态工具目录 `select_tools`）、`demomcp/graph/{skills,tracker_render}.py`（报告 skill 注册表 + 快报确定性模板渲染）、`demomcp/graph/{skill_loader,skill_catalog}.py` + `demomcp/skill_library/claude-for/`（claude-for 63 条技能库：装配器 + 中文短描述表 + vendored 语料）、`demomcp/config/skill_toggle.py`（按技能开关）、`scripts/web/src/components/skills/*`（技能页：卡片/详情侧滑/快速使用）、`scripts/sync_claude_for_skills.py`（语料同步）、`demomcp/agents/agent.py`（薄壳：构图 + ainvoke + 归一化 AgentResult；`registry.py` 同名类为废弃占位）、`demomcp/providers/llm/deepseek.py`（流式/工具/回传 reasoning_content）、`demomcp/providers/tools/mcp.py`（HTTP 连接 + 自动发现/打印工具清单 + 重试/超时 + 权限失败友好提示）、`demomcp/providers/tools/wind.py`（万得 WindToolProvider，**由网关装配**）、`demomcp/providers/tools/composite.py`（通用多源合并，现仅测试引用；网关用 `mcp_gateway/pool.py`）、`mcp_gateway/{config,sources,toggle_store,pool,mcp_endpoint,admin,app}.py`（独立网关：按源开关 + 动态聚合 + 真 MCP 端点 + /admin REST）、`mcp_gateway/providers/{multi_domain,ifind}.py`（`multi_domain` = 「一个数据商 = N 个 MCP 端点」的通用外壳：懒连接/单域隔离/60s 冷却/目录单飞/懒发现三件套，从 wind.py 泛化而来但**不动 wind.py**；`ifind` 只放 iFind 特有的域表、裸 token 头、并发上限与错误信封）、`external_sources/{akshare_server,china_news_server}.py`（免费源，独立进程 + 独立 `requirements.txt`，见其 README）、`scripts/dev_up.ps1`（本地双进程启动：网关带自动重启 + web）、`demomcp/providers/tools/curate.py`（可用性探测 `probe_availability`，默认关）、`demomcp/rag/`（核心 `runtime` / `persist` / `hybrid_retriever` / `citing` / `schemas` + `http_retriever`/`server`；`retriever.py` 为无引用遗留占位）、`scripts/{eval_rag,validate_rag,dba_rag}.py`（离线评估门禁/真引擎验证/建索引）、`scripts/probe_tools.py`（工具可用性探测，产出 `data/tool_catalog.json`，默认关）、`scripts/web/src/`（前端：`components/agent/*` 思考轨迹·工具卡·引用列表·检索漏斗，`lib/sse.ts` SSE 解析，`state/` Zustand）、`demomcp/entry/web.py`（SSE + sessions API + RAG HTTP 端点 + `/api/quickreport/*` + `/api/settings/mcp*`（总闸 + 转发网关按源开关）+ lifespan store/工具池/快报任务）、`demomcp/db/{models,store}.py`（ChatMessage/ChatTurn/ChatTurnData 三表）、`demomcp/config/{settings,env}.py`（settings 含 `mcp_gateway_url` 与 `rag_*` 与 `quickreport_*`；上游源配置已迁往 `mcp_gateway/.env`）、`demomcp/quickreport/`（配置/阈值谓词/计算/取数/持久化/定时/薄壳——`pipeline` 为核心；`server.generate_from` 为一等入口；`source.py` 按工具名判来源与业务失败，**不 import agents/***）、`scripts/web/src/components/report/{panels,charts}/*` + `components/charts/echarts-setup.ts`（快报 ECharts 仪表盘；后者是全站唯一 ECharts 注册点）、`scripts/web/src/lib/{quickReport,quickReportStatus,reportMetrics}.ts`（快报解析/新鲜度/指标）、`data/quickreport/watchlist.json`（标的池配置，提交；`latest.json` 运行时产物已 gitignore）、`scripts/{build_quickreport_watchlist,generate_quickreport}.py`（标的池 bootstrap / 离线独立生成）、`mcp_server/server.py`（后备，默认停用）.

**文档入口**：`README.md`（面向用户/运维的全景：拓扑、快速开始、配置表、端点清单、文档索引）、`mcp_gateway/README.md` + `mcp_gateway/CLAUDE.md`（网关自己的说明与目录级约定——**在 `mcp_gateway/` 下改代码前先看后者**）、`external_sources/README.md`（免费源独立部署与反爬坑）、`docs/SKILLS_{IMPORT_PLAN,INTEGRATION_DECISIONS}.md`（技能库方案与实测基线）、`docs/RAG_{INTEGRATION,FINANCE,VECTORDB}.md`（RAG 现状/蓝图/选型）。
⚠️ `docs/{ARCHITECTURE,TEST_REPORT,tool-call-layer}.md` 核对于 **2026-08-27**，写在网关化之前：五节点状态机与分层描述仍准确，但凡提到「应用直连 `TUSHARE_MCP_URL`」「pytest 169 项」「语义工具层」处**以代码与 `README.md` 为准**。
