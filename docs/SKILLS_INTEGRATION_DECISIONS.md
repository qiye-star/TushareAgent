# 接入 claude-for 技能库——架构决策清单（已拍板归档）

> **本文已完成使命**：§§2 的 Q1–Q4 与后续两轮追问已全部拍板，结论见 [SKILLS_IMPORT_PLAN.md](SKILLS_IMPORT_PLAN.md) 的决策总表（D1–D13）。
> 本文作为**实测事实基线（F1–F4）与被否备选方案的记录**保留，不再更新。

> 日期：2026-09-08
> 关系：本文是 `docs/SKILLS_IMPORT_PLAN.md` 的**前置文档**。那份计划里的 D1–D5 是上一轮的倾向，
> 本轮已对两侧代码与语料做**实测复核**，发现 4 处与那份计划不符的事实（见 §1），其中两处**动摇了原方案的核心选型**。
> 因此这里把接入形态重新摊成选项，**不替你做决定**；你拍板后我把结论合并回 `SKILLS_IMPORT_PLAN.md` 再动代码。

---

## 1. 实测复核：与原计划不符的 4 处事实

| # | 原计划的说法 | 实测结果 | 影响 |
|---|---|---|---|
| F1 | 「导入技能正文 → synthesizer 系统提示词」 | **62/63 正文的主体是「Data Sources + Workflow/Step 1..7」取数工作流**；只有 **14/63** 带 Output/模板小节 | ⚠️ **核心错配**：正文讲的是「先取哪些数据、怎么算」，而 TushareAgent 里取数决策发生在 `tool_rag`，`synthesizer` 只负责写。只喂 synthesizer 等于把取数指令喂给了不取数的节点 → 决策 Q2 |
| F2 | 「router catalog ≤8KB 用全文，超限截断」 | 63 条 description 合计 **20,718 字符**（均 328，最长 433） | ⚠️ router 是**每次提问都跑**的单次分类调用，+20.7KB（≈8–10k tokens）会打到每一句「查下比亚迪股价」上 → 决策 Q1 里的路由形态 |
| F3 | 「工具名翻译是核心工作（44 wind + 31 ifind）」 | 正文里的工具引用**绝大多数是免费源名，与本项目同名**：`get_financials`×119、`get_quote`×62、`get_industry_stocks`×35、`get_index_data`×23、`get_stock_info`×21、`get_historical_data`×21、`get_fund_data`×12、`get_stock_news`×11、`get_market_overview`×8、`search_stock`×6、`get_market_headlines`×3。`ifind_*` 具名约 20 种共 ~50 次，`wind_*` 具名约 15 种共 ~17 次 | ✅ 翻译工作量比预估小一个量级：**免费源零翻译**，只有 wind_/ifind_ 两族需要映射到 3 元工具 → 决策 Q4 影响面变小 |
| F4 | 「文件型技能约 17 个」 | 实测**仅 7 个**：`china-xlsx-author`(9 命中)、`china-ppt-template-creator`(7)、`china-pptx-author`(7)、`china-lbo-model`(6)、`china-3-statement-model`(4)、`china-deck-refresh`(4)、`china-ai-readiness`(3) | 能力标注名单大幅缩小；其中 3 个（xlsx/pptx/ppt-template-creator）**本质就是文件生成器**，导入后基本无功能 → 决策 Q3 |

### 其它已核实的事实（无争议，直接采用）

- **63 个文件 / 58 个唯一 id**：`china-accrual-schedule`、`china-break-trace`、`china-gl-recon`、`china-roll-forward`、`china-variance-commentary` 这 5 个在 `china-finance` 与 `fund-admin` 下各有一份 → **id 会撞名**，loader 必须显式去重策略（决策 Q3 附带）。
- frontmatter 全仓统一只有 `name:` + `description:`（63/63，单行）→ 极简解析器可行，无需 yaml 依赖。
- vertical skills 目录下**除 `SKILL.md` 没有任何其它文件**（0 个 `scripts/`、`references/`）→ claude-for 这批技能没有用 Claude Code 的渐进披露附件，导入即全量正文（body 均 5,830 字符，最长 9,372）。
- `agent-plugins/` 的 31 个 skill 名是 vertical 的**子集**（agent-only = 空集）→ 确认只导 `vertical-plugins/`，无遗漏。
- claude-for 的 `mcp-servers/` 4 个（wind / ifind / akshare / china-news）与本项目网关已接的源**一一对应**，Tushare 官方 247 接口是本项目独有的增量。
- TushareAgent 侧挂载点（已逐行确认）：
  - `nodes.py:658` —— `skill.system_prompt(base_system)` 在 synthesizer **整体替换** `synth_system`（不是追加）→ 导入技能会**丢掉** `prompts.py` 里的三反编造纪律与「正文不写来源名」等既有约束，除非 loader 重新包一层。
  - `nodes.py:329` —— `skill_tools=frozenset(skill.tool_families)` 恒保留、不进 `TOOL_MAX_REVEALED` 限额。
  - `skills.py::build_router_system` —— 逐条 `f"- {s.id}：{s.name} —— {s.description}"` 拼进 router system。
  - `tests/test_agent_loop.py:804` —— `assert [s.id for s in SKILLS] == ["ai_supply_chain_tracker"]`，导入后必改。

---

## 2. 待拍板决策

### Q1 · 接入形态（最关键，决定后面全部）

| 选项 | 做法 | 代价 | 风险 |
|---|---|---|---|
| **A. 轻量 vendored 导入**（≈原 D1+D3） | 63 条 SKILL.md → `Skill` 条目塞进 `SKILLS`，全链路零改动 | 最小：1 个 loader + 语料拷贝，约 1 天 | router system +20.7KB/每次提问；正文喂错节点（F1）；分类精度可能下滑 |
| **B. Skill 运行时（渐进披露）** | 新增 skill 注册表 + **两段路由**：router 只看「短清单」（id + 一句话，约 3KB）→ 命中后才懒加载正文；正文按段分别注入 `tool_rag` 与 `synthesizer` | 中等：改 router 节点 + loader + 分段器，约 2–3 天 | 多一次 LLM 往返（或用关键词预筛省掉）；改动触及 graph 内核 |
| **C. 前端显式选报告类型** | 不靠 router 猜：前端加「报告类型」选择器（沿用快报视图形态），`ChatRequest` 带 `skill` 字段直传 | 中等：前后端各一处 + loader | 用户要先知道有哪些技能；纯自然语言提问不会自动命中 |
| **D. 反向：网关当 MCP server 给真 Claude Code** | 不导入语料；把 `mcp_gateway:8766` 配进 Claude Code，claude-for 插件**原样跑**（它的 Read/Write/xlsx/pptx 在那边都是真的） | 最小：一份配置文档 | TushareAgent 自己的 web/CLI 得不到任何技能；两套入口分裂 |
| **E. B + C 组合** | 运行时 + 前端选择器双入口 | 最大 | —— |

> 我的倾向（供参考，不作决定）：**B**。理由是 F1/F2 两处实测都直接命中 A 的软肋，而 B 的增量主要落在 `nodes.py::make_router` 一个函数 + 新 loader，仍不动网关与 external_sources。若你只想先看到效果，A 可作为 Phase 0 的一次性验证。

### Q2 · SKILL.md 正文注入到哪个节点（F1 的直接后果）

| 选项 | 做法 | 说明 |
|---|---|---|
| a | 只喂 `synthesizer`（原 D3） | 取数工作流指令到不了取数节点；`tool_hint` 只能靠 loader 生成的一句话 |
| b | **按小节拆**：`Data Sources`/`Workflow`/`Key Terms` → `tool_hint`（tool_rag）；`Output`/`Format`/`Citations`+其余 → `system_prompt`（synthesizer） | 最贴合本项目分工；需要一个基于 Markdown 标题的分段器（标题命名不统一，需容错） |
| c | **全文喂两个节点** | 实现最简、无分段风险；代价是 token 翻倍（每技能 ~6KB × 2，且 tool_rag 是多轮循环节点，每轮都带） |
| d | 先由我把 63 篇**压缩改写**成本项目口径的精简模板（取数清单 + 输出结构），只留改写版 | 质量最高、运行成本最低；但等于分叉语料，upstream 同步失效，且是 63 篇的人工/LLM 改写工作量 |

### Q3 · 导入范围

| 选项 | 数量 | 说明 |
|---|---|---|
| a | **63 全量** | 含 5 对撞名（需去重策略）+ 7 个文件产物型（3 个导入后近乎无功能） |
| b | **58 去重全量** | 撞名的 5 个只留一份（留 `fund-admin` 还是 `china-finance` 版本？两份内容需 diff 确认） |
| c | **剔文件型**：58 − 3（xlsx-author / pptx-author / ppt-template-creator）= 55 | 保留 lbo-model / 3-statement-model / deck-refresh / ai-readiness（它们主体仍是分析逻辑） |
| d | **分批**：先 `china-finance` 31 条跑通，再上其余 32 | 风险最低，见效最快 |

附带：撞名去重策略 —— ①保留先声明域（沿用网关 `pool.list_tools` 的「先声明者赢 + WARNING」哲学）；②id 加域前缀（`fund-admin/china-gl-recon`，但 router 输出的 id 会变长）；③人工挑一份。

### Q4 · wind_/ifind_ 旧工具名怎么处理（免费源同名已无需处理，见 F3）

| 选项 | 做法 | 说明 |
|---|---|---|
| a | **注入对照表**（原 D5）：loader 扫正文命中的旧名，生成「旧名 → `wind_query(api_name=…)` / `ifind_query(query=…)`」段追加进提示词；正文零改写 | upstream 同步零摩擦；提示词多一段 |
| b | **网关加别名层**：`mcp_gateway` 注册 `wind_get_stock_fundamentals` 等具名工具，内部转发到 `wind_query` | 语料零改动、LLM 零学习成本；但要在网关维护一张会随远端漂移的具名表（与「懒发现」哲学冲突，且 CLAUDE.md 明令网关不做兼容层） |
| c | **改写正文**里的旧名为网关写法 | 一次性干净；语料分叉 |
| d | **不处理**，靠 Unknown tool 的重试与 `base_system_for` 的各源用法指南自愈 | 零成本；质量波动，浪费轮次 |

---

## 3. 第二轮（Q1–Q4 定了再问）的遗留问题

- R1 router 短清单怎么生成：description 首句截断 / 我逐条改写一句话 / 按域两级路由（先选域再选技能）。
- R2 导入技能是否开 `should_rag`（年报 RAG 只在解析出语料公司时才检索，目前语料只有比亚迪/宁德时代）。
- R3 是否给部分技能写确定性 `render`（如 `china-earnings-analysis`，沿用 tracker 模式）——本轮建议不做。
- R4 语料同步：`scripts/sync_claude_for_skills.py` 要不要写；upstream commit 记录到库内 README（当前 claude-for HEAD = `59e97ee`）。
- R5 前端是否展示命中的技能名 / 报告类型标签（`report_type` 已有字段）。
- R6 `SKILL_LIBRARY_ENABLED` 逃生开关默认开还是默认关。
- R7 验收标准：`SKILLS` 长度断言的目标值、router 命中冒烟组要覆盖哪几个技能。

---

## 4. 三条不变的边界（无需决策，沿用原计划 §6）

- 不动 `external_sources/`；
- 不改 `E:\claude-for-financial-services-cn` 仓库；
- 网关**是否**改动取决于 Q4 选 b（唯一一个会碰网关的选项），其余选项下网关零改动。
