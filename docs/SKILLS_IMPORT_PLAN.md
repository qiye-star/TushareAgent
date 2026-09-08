# 接入 claude-for 技能库——实施方案 v2

> 日期：2026-09-08 · upstream `claude-for-financial-services-cn` HEAD = `59e97ee`
> 目标：把 claude-for 的 **63 个 SKILL.md 投研技能**接入 TushareAgent，让 web/CLI 智能体除「快报」外
> 还能生成 DCF、可比估值、业绩点评、IC memo、客户持仓报告等结构化投研报告；并在前端新增**技能广场页**
> （参考 Wind Alice `alice.wind.com.cn/skills` 形态：卡片 → 详情 → 快速使用 → 回到对话）。
> **不动 `external_sources/`、不动 `mcp_gateway/`、不改 claude-for 仓库。**
>
> 事实基线与被否掉的备选方案见 [SKILLS_INTEGRATION_DECISIONS.md](SKILLS_INTEGRATION_DECISIONS.md)（含 4 处实测纠错 F1–F4）。
> v1 的 D1–D5 已被本轮实测推翻两条（正文注入位置、router 目录成本），下表为最终结论。

---

## 0. 决策总表（已拍板）

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 接入形态 | **Skill 运行时（渐进披露）+ 前端显式入口** 双通道。router 只看「短清单」，命中后才加载正文；同时前端技能页可「快速使用」直接指定 |
| D2 | 语料载体 | **vendored 拷贝入库**（`demomcp/skill_library/claude-for/`），不依赖 `E:\claude-for-financial-services-cn` 运行；不加 submodule |
| D3 | 正文注入位置 | **按 Markdown 小节拆**：`Data Sources`/`Workflow`/`Key Terms` → `tool_hint`（tool_rag）；`Purpose`/`Output`/`Format`/`Citations`/其余 → `system_prompt`（synthesizer） |
| D4 | 导入范围 | **63 条全量**；5 对跨域撞名**两份都留**，`fund-admin` 那 5 条 id 加 `-fund` 后缀 |
| D5 | 工具名翻译 | **loader 注入对照表**：扫正文命中的 `wind_*`/`ifind_*` 旧具名 → 生成「旧名 → `wind_query(api_name=…)` / `ifind_query(query=…)`」段；**正文零改写**。免费源 11 个工具名与本项目同名，零翻译 |
| D6 | router 短清单 | **逐条改写一句中文短描述**（63 条，落成 vendored 元数据表），全量 63 行一次给 router；不做关键词预筛、不做两级路由 |
| D7 | 年报 RAG | `should_rag=True` 的 **16 条**（核心 11 + 扩展 5，名单见 §3.2f），其余 False |
| D8 | 文件产物型技能 | 保留并**自动**加「本环境无文件读写/Excel/PPT 工具」能力降级标注（规则判定，实测命中 7 条） |
| D9 | 既有纪律 | loader **强制包一层本项目纪律段**（`skill.system_prompt` 在 synthesizer 是整体替换，不包就会丢三反编造纪律与「正文不写来源名」） |
| D10 | 逃生开关 | `SKILL_LIBRARY_ENABLED` **默认开** |
| D11 | 前端技能页 | Sidebar **第三个视图「技能」**（聊天 / 快报 / 技能 / 设置）；卡片列表 + 详情侧滑 + **每技能启用/停用开关** + 「快速使用」 |
| D12 | 快速使用语义 | **强制本轮用该 skill，发送后自动清除**（router 跳过 skill 判定，intent 仍由 router 定） |
| D13 | 命中反馈 | 思考轨迹里展示「命中技能：xxx」；写 `scripts/sync_claude_for_skills.py` 供开发期刷新语料 |

---

## 1. 事实基线（摘要，详见 DECISIONS 文档）

- 63 个 `SKILL.md`，6 个域：china-finance 31 / investment-banking 10 / private-equity 9 / fund-admin 6 / wealth-management 5 / operations 2。
- frontmatter 全仓统一只有 `name:` + `description:`（63/63 单行）→ 极简解析器，无需 yaml 依赖。
- skills 目录下**除 `SKILL.md` 无任何附件**（0 个 `scripts/`/`references/`）→ 导入即全量正文。
- body 均 5,830 字符（最长 9,372，合计 367KB）；description 均 328 字符（合计 20.7KB）→ **原样拼进 router 提示词不可接受**，故 D6。
- 正文结构：**62/63 有 `Workflow`/`Step` 小节、61/63 有 `Data Sources` 小节，仅 14/63 有 `Output`/模板小节** → 正文主体是取数工作流，故 D3。
- 正文工具引用：免费源名占绝对多数（`get_financials`×119、`get_quote`×62、`get_industry_stocks`×35、`get_index_data`×23、`get_stock_info`×21、`get_historical_data`×21、`get_fund_data`×12、`get_stock_news`×11、`get_market_overview`×8、`search_stock`×6、`get_market_headlines`×3）**与本项目 `external_sources/` 同名**；`ifind_*` 约 20 种/50 次、`wind_*` 约 15 种/17 次需映射到 3 元工具。
- `agent-plugins/` 的 31 个 skill 名是 vertical 的**子集**（agent-only = 空集）→ 只导 `vertical-plugins/`。
- 5 对撞名是**同名的两个不同技能**（`china-finance` = 上市公司口径 / `fund-admin` = 基金组合口径，Workflow 步骤完全不同）→ 故 D4 两份都留。
- TushareAgent 侧挂载点（逐行确认）：`nodes.py:658`（`skill.system_prompt` 整体替换 `synth_system`）、`nodes.py:329`（`skill_tools` 恒保留不进 `TOOL_MAX_REVEALED`）、`skills.py::build_router_system`（逐条拼 description）、`tests/test_agent_loop.py:804`（`SKILLS` 断言，必改）。

---

## 2. 目标架构

```
demomcp/
├── graph/
│   ├── skills.py            # 改：SKILLS = BUILTIN + load_skills()；build_router_system 用 catalog_line
│   ├── skill_loader.py      # 新：frontmatter 解析 / 小节分段 / 工具名对照 / 能力标注 / 纪律包裹 / Skill 组装
│   ├── skill_catalog.py     # 新：63 条中文短描述表（D6 的 vendored 元数据，人工维护）
│   ├── nodes.py             # 改：make_router 支持 forced_skill 跳过判定；on_process 带命中技能名
│   ├── state.py             # 改：GraphState 加 forced_skill
│   └── …（routes/builder/tool_select/prompts 零改动）
├── skill_library/           # 新：vendored 语料（提交入库，~367KB 纯文本）
│   └── claude-for/
│       ├── README.md        # upstream commit（59e97ee）/ 同步方式 / 「正文不得手改」声明
│       └── vertical-plugins/<域>/skills/<技能>/SKILL.md   # 63 个，与 upstream 同构可 diff
├── skills_toggle.py         # 新：每技能启用/停用持久化（镜像 mcp_gateway/toggle_store.py 形态）
├── config/settings.py       # 改：skill_library_enabled / skill_library_dir / skills_toggle_path
└── entry/web.py             # 改：ChatRequest.skill；GET /api/skills、GET /api/skills/{id}、POST /api/skills/{id}/toggle

scripts/web/src/
├── App.tsx                  # 改：MainView 加 'skills'
├── components/layout/Sidebar.tsx        # 改：第三个入口「技能」
├── components/skills/                   # 新
│   ├── SkillsView.tsx      # 按域分组的卡片网格 + 搜索 + 每卡开关
│   ├── SkillCard.tsx
│   └── SkillDetail.tsx     # SideSheet（已有组件）：全文 Markdown + 快速使用按钮
├── components/chat/Composer.tsx         # 改：已选 skill 芯片（可 x 取消）
├── components/agent/ThinkingTrace.tsx   # 改：展示「命中技能：xxx」
├── hooks/useSkills.ts                   # 新：列表/详情/开关
├── lib/api.ts / lib/sse.ts              # 改：skill 字段
└── state/useChatStore.ts                # 改：send(query, {skill})，发完清空

scripts/sync_claude_for_skills.py        # 新（dev）：--src 刷新拷贝 + diff 提示 + --dry-run
tests/test_skill_loader.py               # 新：解析/分段/映射/标注/纪律/去重/短清单长度 全量锁
tests/test_skill_api.py                  # 新：/api/skills 三端点 + forced skill 透传
tests/test_agent_loop.py                 # 改：SKILLS 断言；新增 forced_skill 路由用例
```

**数据流（新增部分标 ★）**：

```
浏览器 ──「技能」页 ★ GET /api/skills ─────────────► web.py ── skills.SKILLS + skills_toggle
   │                                                          （停用的不返回 enabled）
   └─「快速使用」→ 回到对话页，Composer 挂芯片 ★
        └─ POST /chat {message, skill:"china-dcf"} ★
             └─ Agent.run(forced_skill=…) ★
                  └─ router：forced_skill 非空 → 跳过 skill 判定，只定 intent ★
                       └─ tool_rag：select_system(base, intent, skill.tool_hint)   ← SKILL.md 取数段
                            └─ synthesizer：skill.system_prompt(base)               ← SKILL.md 输出段
```

自然语言路径不变：router 看 63 行短清单 → 命中填 id → 后续同上。

---

## 3. 后端详设

### 3.1 语料落点（D2）

- 结构照搬 upstream：`demomcp/skill_library/claude-for/vertical-plugins/<域>/skills/<技能>/SKILL.md`，整目录提交 git。
- 库内 `README.md` 记：upstream repo + commit `59e97ee` + 同步日期 + **「正文不得手改，一切改写走 loader 层」**。
- 运行期**不读** `E:\claude-for-financial-services-cn`；`scripts/sync_claude_for_skills.py --src <path> [--dry-run]` 仅开发期刷新，输出逐文件 diff 摘要。

### 3.2 `demomcp/graph/skill_loader.py`（核心新增）

**(a) frontmatter 解析**——只认首行 `---` + `name:`/`description:` 两键（实测 63/63 一致），失败/缺键 → WARNING 跳过该文件，**不抛异常**（沿用「配置没配好不崩进程」原则）。

**(b) 小节分段器（D3）**——按 `^#{2,3} ` 标题切块，白名单进 `tool_hint`，其余进 `system_prompt`：

| 去向 | 标题匹配（大小写不敏感，中英双语） |
|---|---|
| `tool_hint`（tool_rag） | `Data Sources` / 数据源 / `Tools` / `Key Financial Terms` / `Key Terms` / 术语，以及 `Workflow` 下**除写作步骤外**的 `Step N` |
| `system_prompt`（synthesizer） | `Purpose` / `Output` / `Deliverable` / `Template` / 模板 / `Format` / `Source Citations` / `China-Specific*` / **`Step N` 中标题含 `Draft`/`Report`/`Write`/`Commentary`/`Quality Check`/输出 的写作步骤** / 未命中白名单的一切小节 |

> 写作步骤单独归给 synthesizer 是对 D3 的细化：实测 `Step 6: Draft the Report` 这类步骤才承载输出结构（因为只有 14/63 有独立 Output 小节），归给取数节点会让 synthesizer 拿不到报告骨架。
> `tool_hint` 有硬上限 `_MAX_SKILL_HINT_CHARS = 3000`（超出按 `Step` 边界截断 + 尾部标注）——因为 `tool_rag` 是**多轮循环节点，每轮都带这段**，不设限会在 10 轮里翻十倍。

**(c) 工具名对照表（D5）**——三类规则，`mcp__<server>__<tool>` 先归一为 `<tool>` 再查表：

1. `wind_<name>` → `wind_query(api_name="<name>", params={…})`，翻译文本注明「**`api_name` 以 `wind_list_apis` 返回为准**」（防远端目录漂移，与网关懒发现哲学一致）；
2. `ifind_<name>` → `ifind_query(query="…自然语言…")`，注明「iFind 只吃一个自然语言 query，参数风格见各源用法指南」；
3. 免费源 11 个（`search_stock`/`get_quote`/`get_historical_data`/`get_financials`/`get_industry_stocks`/`get_index_data`/`get_stock_info`/`get_market_overview`/`get_fund_data`/`get_stock_news`/`get_market_headlines`）→ **同名直读**，但对可被 Tushare 官方替代的附「官方等价建议」：`get_financials`→`income`/`fina_indicator`、`get_historical_data`→`daily`、`get_quote`→`daily_basic`、`get_stock_news`→`news`（有权限时）——引导优先官方源，权限失败再退免费源。

命中的条目逐条生成「工具名对照」段追加进 `system_prompt` 与 `tool_hint`；**零命中则不输出该段**；未命中映射表的工具名**原样保留，不删不造**。

**(d) 能力降级标注（D8）**——**纯规则、不维护手工名单**（防漂移）：正文命中 `xlsx|pptx|PowerPoint|Excel|spreadsheet|docx|openpyxl|python-pptx|Read tool|Write tool`（忽略大小写）**≥3 次**即标注。实测命中 7 条：`china-xlsx-author`(9) / `china-ppt-template-creator`(7) / `china-pptx-author`(7) / `china-lbo-model`(6) / `china-3-statement-model`(4) / `china-deck-refresh`(4) / `china-ai-readiness`(3)。标注文本：

> 【本环境能力限制】运行环境无文件读写 / Excel / PPT 生成与审计工具（无 Read/Write/xlsx/pptx 能力）。本技能涉及文件产物的部分只输出**内容与结构建议**（Markdown 表格），不生成 `.xlsx/.pptx/.docx` 文件，也不要声称已生成；所有数值只能来自下文『可用数据』，不得编造。

**(e) id 去重（D4）**——`vertical-plugins/fund-admin/` 下与 `china-finance/` 撞名的 5 条（`china-accrual-schedule`/`china-break-trace`/`china-gl-recon`/`china-roll-forward`/`china-variance-commentary`）id 加 `-fund` 后缀；loader 里保留一张显式 `ID_SUFFIX_BY_DOMAIN` 表 + 一条兜底断言「组装完 id 必须全局唯一，撞了就抛」（防将来 upstream 新增撞名被静默吞掉）。

**(f) `should_rag` 名单（D7，16 条）**：

```
核心 11：china-3-statement-model  china-comps  china-comps-analysis  china-dcf
         china-dcf-model  china-earnings-analysis  china-earnings-preview
         china-initiating-coverage  china-lbo-model  china-model-update  china-thesis-tracker
扩展  5：china-merger-model  china-unit-economics  china-competitive-analysis
         china-ic-memo  china-sector-overview
```

`strategy`：名单内取 `factual`（财报数值题走 chunk 路），其余 `auto`。
（提醒：RAG 只在 `infer_filters(q).company` 非空时才检索，当前语料只有比亚迪/宁德时代，其它标的自动空返、不影响。）

**(g) `system_prompt(base)` 装配顺序（D9 —— 顺序即优先级，测试锁定）**

1. `base`（domain 基础提示）
2. **本项目纪律段（强制）**：只依据『可用数据』、不编造数字/来源/结论；正文不写接口名/来源名（来源由界面统一展示）；取不到标『数据未接入』；评级用方向性判断、目标价与证据强度匹配、盈利预测取到真实数据才填（即 `prompts.py` 的三反编造纪律）
3. **工具名对照段**（命中才有）
4. **能力降级段**（文件产物型才有）
5. **SKILL.md 输出侧正文**（去掉首行 `# 标题` 冗余标题）

（`today_context()` 与免责声明由 synthesizer 在末尾自动追加，loader 不管。）

**(h) `tool_families`**——由正文命中的源族保守推导：命中 `wind_*` → `("wind_",)`；命中 `ifind_*` → `("ifind_",)`；命中免费源哨兵名 → 该**精确名**（不用子串，避免 `get_quote` 误伤一片）。Tushare 官方接口**不做**硬保留，交给 `tool_select._TOPIC_KEYWORDS` 的关键词机制（财务/估值/行情词天然命中 `income`/`fina_indicator`/`daily`）。
理由：`_TOPIC_KEYWORDS` 只认 Tushare 官方接口名子串，`wind_query`/`ifind_query` 平时几乎不可能被 `_score` 选中 → 依赖 Wind/iFind 的技能必须靠 `tool_families` 进恒保留集。

**(i) `catalog_line`（D6）**——`Skill` 新增可选字段 `catalog_line: str | None`，`build_router_system` 优先用它（`None` 回落 `description`，现有 tracker 不受影响）。内容取自 `skill_catalog.py` 的 63 条人工中文短描述（每条 ≤40 字），目标 router 清单 **≤4KB、硬上限 6KB**（测试断言）。缺表项 → 回落 description 首句截断 160 字符 + WARNING。

**(j) 其余字段**：`render=None`（走 LLM 路径）、`report_type=f"library:{domain}"`、`name` 取正文首行标题或 frontmatter name。

### 3.3 `demomcp/graph/skills.py`（最小修改）

- `SKILLS = BUILTIN_SKILLS + load_skills()`；`load_skills(dir=None)` 默认读包内 `skill_library/claude-for/`，参数/env 可换目录；`SKILL_LIBRARY_ENABLED=false` → 只剩 builtin（逃生通道，默认开）。
- `Skill`/`build_router_system`/`get_skill` 签名保持；`build_router_system` 内**过滤停用技能**（见 3.4）。
- import 期读 63 个小文件冷启动 <50ms，可接受。

### 3.4 每技能启用/停用（D11）

- `demomcp/skills_toggle.py`：镜像 `mcp_gateway/toggle_store.py` 形态——JSON 落 `data/skills_toggle.json`（运行时产物，gitignore），缺文件 = 全部启用；读写加锁、写失败只 WARNING 不崩。
- 生效点两处：①`build_router_system` 只拼启用的（停用即不参与自然语言路由）；②forced skill 若指向停用技能 → API 返回 **409**，`/chat` 侧忽略并按普通路由走（不静默假装用了）。
- 内置 `ai_supply_chain_tracker` 也纳入开关（保持一致），默认启用。

### 3.5 router 节点改动（D1 渐进披露 + D12 forced skill）

- `GraphState` 加 `forced_skill: str | None`；`Agent.run(..., forced_skill=…)` 透传（`agents/agent.py` 已有 `mode` 透传的先例）。
- `make_router`：`forced_skill` 非空且解析到启用技能 → **不把技能清单拼进 system**（省 4KB）、只做 intent + `out_of_scope` 判定，`skill` 直接填 forced 值；否则原逻辑 + 短清单。
- `on_process("intent", …)` 已带 `skill` 字段 → 前端展示命中技能名（D13）只需前端改（`ThinkingTrace`）+ 一份 id→中文名映射（走 `/api/skills` 拉一次缓存）。
- **与 `mode` 的关系**：`mode="quick"` 时 `agent.py` 现在传 `skills=[]`（禁用技能）。forced skill 与 quick 模式冲突 —— 见 §10 待确认项 O1。

### 3.6 API（`entry/web.py`）

| 端点 | 行为 |
|---|---|
| `GET /api/skills` | 返回 `[{id, name, name_zh, domain, catalog_line, report_type, enabled, files_limited, should_rag, tool_families}]`，按域分组由前端做 |
| `GET /api/skills/{id}` | 加上 `body_markdown`（SKILL.md 正文原文，供详情页渲染）、`tool_mapping`（命中的对照条目）、`capability_note` |
| `POST /api/skills/{id}/toggle` | `{enabled: bool}` → 写 toggle store，返回新状态；未知 id → 404 |
| `POST /chat` | `ChatRequest` 加 `skill: str | None`；未知/停用 id → 忽略（不 500），并在 `process` 事件里说明 |

（形态刻意与既有 `/api/settings/mcp/sources` 一致，前端可复用 `Switch` 与请求封装。）

---

## 4. 前端详设（D11 / D12 / D13）

- `App.tsx`：`MainView = 'chat' | 'report' | 'skills' | 'settings'`；`Sidebar` 第三个入口「技能」（收起态图标，沿用现有两图标按钮形态）。
- `components/skills/SkillsView.tsx`：
  - 顶部搜索框（匹配 id / 中文名 / 短描述）+ 域筛选 chips（6 个域 + 全部）；
  - 卡片网格：中文名 + 短描述 + 域 Badge + 「文件受限」Badge（D8 命中者）+ 右上 `Switch` 启用开关；
  - 点卡片 → `SkillDetail`（复用已有 `SideSheet`）：全文 `react-markdown` 渲染（`remark-gfm` + `remark-breaks`，与聊天区同一套，天然防 XSS）+ 「工具名对照」「能力限制」两个折叠块 + 底部主按钮「快速使用」。
- 「快速使用」：`setView('chat')` + `useChatStore.setPendingSkill({id, name_zh})` → `Composer` 顶部挂芯片（可 `x` 取消）→ `send(query, {skill})` → **发送后自动清空**（D12）。
- `ThinkingTrace`：`process.intent` 事件里 `skill` 非空 → 首行加「命中技能：<中文名>」。
- `lib/api.ts` 加 `getSkills/getSkill/toggleSkill`；`hooks/useSkills.ts` 负责拉取+缓存+乐观开关。

---

## 5. 实施步骤

### Phase 1 —— 语料 + loader（不接线）
1. 拷贝 63 个 `SKILL.md` 到 `demomcp/skill_library/claude-for/`，写库内 README（记 `59e97ee`）。
2. 写 `skill_catalog.py` 的 63 条中文短描述（D6，我逐条改写）。
3. 写 `skill_loader.py`（a–j 十项）+ `tests/test_skill_loader.py`（纯离线：63 条全解析 / id 唯一含 5 个 `-fund` / 分段白名单 / 对照段含「以 list_apis 为准」/ 7 条能力标注 = 规则输出 / 每条都含纪律段 / `tool_hint` ≤3000 / router 清单 ≤4KB）。
3. 跑 `pytest tests/test_skill_loader.py -q` + `ruff`。

### Phase 2 —— 接线（后端）
4. `skills.py` 合并 `SKILLS`；`skills_toggle.py`；`settings.py` 三配置；改 `tests/test_agent_loop.py:804`（改为「tracker 恒在 + 库内 63 条 id 与目录一致」）。
5. `state.py`/`nodes.py`/`agents/agent.py` 的 `forced_skill` 链路 + `on_process` 命中名；`web.py` 三端点 + `ChatRequest.skill`；`tests/test_skill_api.py`。
6. 全量 `pytest tests -q` + `ruff check demomcp mcp_gateway tests scripts external_sources`。

### Phase 3 —— 前端
7. `MainView` + Sidebar 入口 + `components/skills/*` + `hooks/useSkills` + Composer 芯片 + ThinkingTrace 命中行。
8. `npm run build` 通过；手工点检：列表/搜索/筛选/开关/详情/快速使用/芯片取消。

### Phase 4 —— 冒烟 + 文档
9. 冒烟 4 例（真实链路需网关在跑；离线部分用 MockLLM 单测）：
   - 「给比亚迪做 DCF 估值」→ 自然语言命中 `china-dcf` → tool_rag 取 `income`/`fina_indicator`/`daily_basic` → 输出含纪律段约束的模型；
   - 技能页选 `china-earnings-analysis` → 快速使用 → forced skill 生效（router 不再拼清单）；
   - 命中 `china-xlsx-author` → 输出带能力限制、不声称生成文件；
   - 「生成 AI 算力产业链今日跟踪快报」→ 仍命中 tracker、仍走确定性 render（回归）。
10. 更新 `CLAUDE.md`（skill 小节 + 新增关键坑）、`docs/ARCHITECTURE.md`、库内 README；`scripts/sync_claude_for_skills.py`。

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 64 条 skill 让 router 分类精度下滑 | 该命中的不命中 / 单点查询被误判成报告 | 短清单（≤4KB）+ 每技能开关（可关掉噪声源）+ 前端 forced skill 兜底 + 冒烟命中组 |
| `tool_hint` 每轮循环都带，token 膨胀 | 10 轮时成本翻倍 | `_MAX_SKILL_HINT_CHARS=3000` 按 Step 边界截断（测试锁定） |
| 分段器把输出骨架切进 tool_hint | synthesizer 拿不到报告结构 | 写作步骤（Draft/Report/Write/Commentary/Quality Check）显式归 synthesizer；未命中白名单默认归 synthesizer（保守侧） |
| 正文旧工具名诱导调不存在的工具 | Unknown tool → 重试/自愈，质量波动 | 对照段 + `base_system_for` 各源用法指南双保险；Unknown 有友好错误与重试兜底、不崩 |
| `system_prompt` 整体替换丢掉既有纪律 | 编造数字/写来源名 | D9 纪律段强制包裹，测试锁定每条导入技能都含该段 |
| 文件产物型技能假承诺「已生成 xlsx」 | 用户困惑 | D8 能力标注（规则判定）+ 冒烟抽查 |
| 技能要的数据根本取不到（DCF 需完整三表而权限受限） | 证据空 → fallback/direct_answer | 现有 `no_progress` 守卫 + 不编造纪律；属既有行为 |
| upstream 语料更新后 vendored 落后 | 与 claude-for 漂移 | `scripts/sync_claude_for_skills.py` + 库内 README 记 commit；短描述表缺项自动回落 + WARNING |
| 撞名的 5 条 `-fund` 后缀让 router 输出 id 变长 | 极小 | 短描述里明确「基金/组合口径」，两条描述本来就写明了口径差异 |

---

## 7. 验收标准

1. `pytest tests -q` 全绿（新增 `test_skill_loader.py`、`test_skill_api.py` + 更新后的现有用例）；
2. `ruff check demomcp mcp_gateway tests scripts external_sources` 干净；
3. `python -c "from demomcp.graph.skills import SKILLS; print(len(SKILLS))"` == **64**（1 builtin + 63 导入）；
4. `build_router_system(SKILLS)` 长度 ≤6KB（目标 ≤4KB）；
5. `npm run build` 通过；技能页六项手工点检通过；
6. Phase 4 冒烟 4 例通过；
7. `SKILL_LIBRARY_ENABLED=false` 时 `len(SKILLS) == 1` 且现有行为逐字节不变；
8. 本文档、`CLAUDE.md`、库内 README 与实现一致。

---

## 8. 明确不做

- 不改造 `mcp_gateway`、不加网关具名工具兼容层（D5 选了 loader 侧对照表）；
- 不动 `external_sources/`；不改 claude-for 仓库；
- 不导入 `agent-plugins/`（31 个名是 vertical 的子集）、不导入 `managed-agent-cookbooks/`、不接 claude-for 的 `mcp-servers/`（本项目网关已有等价源）；
- 不把导入技能升级为确定性 `render`（后续可按 tracker 模式对 `china-earnings-analysis` 等单独做）；
- 不做按域批量开关（只做单技能开关；`report_type` 已留 domain 信息，后续可加）；
- 不改写 vendored 正文（一切改写走 loader）。

---

## 9. 两处细节（已拍板）

- **O1 · forced skill 与 `mode="quick"` 冲突** → **前端自动切 + 后端保护（双保险）**：前端在选中技能时把模式选择器自动切到「智能体模式」并在芯片旁提示；后端 `web.py`/`agent.py` 再加一道保护——`forced_skill` 非空时忽略 `mode="quick"`、按 agent 模式跑（`skills` 不置空）。测试锁定后端那道保护。
- **O2 · 技能卡片的数据可达性** → **只标 Badge、不置灰**：正文命中 `ifind_*`/`wind_*` 的技能在卡片上标「需 iFind」/「需 Wind」（loader 已有源族推导，复用 `tool_families`）；即使该源在设置页被停用，技能仍可用、不禁用（多数技能能降级到 Tushare 官方或免费源等价接口）。
