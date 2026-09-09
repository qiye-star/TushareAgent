# claude-for 技能语料（vendored，请勿手改正文）

来源：`claude-for-financial-services-cn`（本地 git 仓库，Claude Code 插件生态）
同步日期：2026-09-08 · upstream commit：**`59e97ee`**（"sync: 同步 20 个 agent-plugin SKILL.md — Wind Tier-0 + fund-admin 描述更新"）
范围：`vertical-plugins/<域>/skills/<技能>/SKILL.md` —— **63 个文件**，6 个域
（china-finance 31 / investment-banking 10 / private-equity 9 / fund-admin 6 / wealth-management 5 / operations 2）

## 为什么只有 SKILL.md

- upstream 的 `agent-plugins/` 下有 124 个 SKILL.md，但其中 31 个技能名是 `vertical-plugins/` 的**子集**
  （agent-only = 空集），是 vendored 拷贝 → 不导入。
- `.claude-plugin/plugin.json`、`.mcp.json` 是 Claude Code 的打包/挂载物，本项目按自己的方式装配数据源
  （`mcp_gateway/`），不需要它们。
- 这批技能**没有** `scripts/`、`references/` 附件（实测 0 个）→ 导入即全量正文，没有渐进披露附件要跟。

## 规则：正文不得手改

一切改写都发生在 **loader 层**（`demomcp/graph/skill_loader.py`）：小节分段、工具名对照、能力降级标注、
本项目输出纪律，全部是在正文外面「包一层」。这样 upstream 更新时可以直接 diff/覆盖，零合并冲突。

需要改语料本身的措辞？改 `demomcp/graph/skill_catalog.py` 的中文短描述，或在 loader 里加规则——**不要动这些 md**。

## 刷新

```bash
uv run scripts/sync_claude_for_skills.py --src E:/claude-for-financial-services-cn --dry-run
uv run scripts/sync_claude_for_skills.py --src E:/claude-for-financial-services-cn
.venv/Scripts/python.exe -m pytest tests/test_skill_loader.py -q     # 必跑
```

同步后如果 upstream 新增/改名了技能，`skill_catalog.py` 会缺条目 → loader 打 WARNING 并回落
「description 首句截断」（router 清单立刻变长且中英混杂）。`tests/test_skill_loader.py::test_catalog_covers_every_loaded_skill`
专门守这件事，别跳过。

## 5 对撞名不是重复

`china-accrual-schedule` / `china-break-trace` / `china-gl-recon` / `china-roll-forward` /
`china-variance-commentary` 在 `china-finance` 与 `fund-admin` 下**各有一份，内容差异很大**
（上市公司口径 vs 基金组合口径，Workflow 步骤完全不同）→ 两份都导入，`fund-admin` 那 5 条 id 加 `-fund` 后缀。
