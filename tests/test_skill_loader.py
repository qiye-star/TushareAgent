"""claude-for 技能库加载器：解析 / 分段 / 工具名对照 / 能力标注 / 纪律段 / 去重 / 短清单长度。

纯离线（只读 vendored 语料，不连 LLM/网关）。这些断言锁住的是**方案里的硬约束**（docs/SKILLS_IMPORT_PLAN.md）：
router 清单不许膨胀、tool_hint 不许无限长、导入技能不许丢掉本项目的不编造纪律。
"""

from __future__ import annotations

from demomcp.graph.skill_catalog import CATALOG, DOMAIN_LABELS
from demomcp.graph.skill_loader import (
    _FILES_MIN_HITS,
    _FILES_PAT,
    _MAX_SKILL_HINT_CHARS,
    CAPABILITY_NOTE,
    LIBRARY_DISCIPLINE,
    RAG_SKILL_IDS,
    Block,
    detect_source_families,
    extract_tool_names,
    load_library,
    parse_frontmatter,
    route_block,
    split_blocks,
)
from demomcp.graph.skills import SKILLS, build_router_system

LIB = load_library()
BY_ID = {ls.id: ls for ls in LIB}


# ---------------------------------------------------------------------------
# 解析与去重
# ---------------------------------------------------------------------------


def test_library_loads_all_63_with_unique_ids() -> None:
    """63 个 SKILL.md 全部解析成功；5 对跨域撞名靠 `-fund` 后缀共存（两份是不同技能，不能去重）。"""
    assert len(LIB) == 63
    assert len({ls.id for ls in LIB}) == 63
    assert sorted(ls.id for ls in LIB if ls.id.endswith("-fund")) == [
        "china-accrual-schedule-fund",
        "china-break-trace-fund",
        "china-gl-recon-fund",
        "china-roll-forward-fund",
        "china-variance-commentary-fund",
    ]
    # 撞名的两份内容确实不同（上市公司口径 vs 基金组合口径）——去重会丢能力
    assert BY_ID["china-gl-recon"].body != BY_ID["china-gl-recon-fund"].body


def test_every_skill_has_description_and_domain_label() -> None:
    for ls in LIB:
        assert ls.description.strip(), ls.id
        assert ls.catalog_line.strip(), ls.id
        assert ls.domain in DOMAIN_LABELS, ls.domain
        assert ls.domain_label == DOMAIN_LABELS[ls.domain]
        assert ls.report_type == f"library:{ls.domain}"


def test_frontmatter_parses_folded_block_scalar() -> None:
    """china-pptx-author 用的是 YAML 折叠块（`description: >` + 缩进续行），单行解析器会漏掉它。"""
    fm = parse_frontmatter("---\nname: x\ndescription: >\n  line one\n  line two\n---\n\nbody\n")
    assert fm is not None
    assert fm["description"] == "line one line two"
    assert fm["body"] == "body"
    assert BY_ID["china-pptx-author"].description.startswith("Generic PowerPoint authoring")


def test_frontmatter_rejects_broken_input() -> None:
    assert parse_frontmatter("no frontmatter") is None
    assert parse_frontmatter("---\nname: x\n") is None          # 未闭合
    assert parse_frontmatter("---\nname: x\n---\nbody") is None  # 缺 description


def test_catalog_covers_every_loaded_skill() -> None:
    """中文短描述表必须齐（缺项会回落 description 首句，router 清单立刻变长且中英混杂）。"""
    missing = sorted(ls.id for ls in LIB if ls.id not in CATALOG)
    assert missing == [], f"skill_catalog 缺条目：{missing}"
    stale = sorted(set(CATALOG) - {ls.id for ls in LIB})
    assert stale == [], f"skill_catalog 有多余条目（语料已删？）：{stale}"


# ---------------------------------------------------------------------------
# 分段（D3）
# ---------------------------------------------------------------------------


def test_split_blocks_ignores_headings_inside_code_fences() -> None:
    """正文在 ```bash 里用 `# 注释`，当成标题会把一节切碎（china-market-data 里有 10+ 处）。"""
    md = "## Data Sources\ntext\n\n```bash\n# iFind — 自然语言选股\nifind_query(...)\n```\n\n## Workflow\nsteps\n"
    titles = [b.title for b in split_blocks(md)]
    assert titles == ["Data Sources", "Workflow"]


def test_route_block_data_to_hint_and_writing_to_output() -> None:
    assert route_block(Block(2, "Data Sources", "", ""), skill_id="x", skill_name="x") == "hint"
    assert route_block(Block(2, "Key Financial Terms", "", ""), skill_id="x", skill_name="x") == "hint"
    assert route_block(Block(3, "Step 1: Pull the Earnings Print", "", "Workflow"), skill_id="x", skill_name="x") == "hint"
    # 写作步骤留给 synthesizer——只有 14/63 有独立 Output 小节，输出骨架多在「Step N: Draft the Report」里
    assert route_block(Block(3, "Step 6: Draft the Report", "", "Workflow"), skill_id="x", skill_name="x") == "out"
    assert route_block(Block(2, "Output Format", "", ""), skill_id="x", skill_name="x") == "out"
    assert route_block(Block(2, "Purpose", "", ""), skill_id="x", skill_name="x") == "out"
    # 与技能名重复的空 H1 丢弃（部分文件出现两次）
    assert route_block(Block(1, "china-comps", "", ""), skill_id="china-comps", skill_name="china-comps") == "drop"


def test_earnings_analysis_segmentation_lands_on_both_sides() -> None:
    ls = BY_ID["china-earnings-analysis"]
    assert "Data Sources" in ls.hint_text and "Key Financial Terms" in ls.hint_text
    assert "Step 1: Pull the Earnings Print" in ls.hint_text
    assert "Step 6: Draft the Report" in ls.output_text
    assert "Purpose" in ls.output_text


def test_tool_hint_is_capped() -> None:
    """tool_rag 是多轮循环节点，每轮都带 tool_hint —— 不设限会在 10 轮里翻十倍。"""
    for ls in LIB:
        assert len(ls.hint_text) <= _MAX_SKILL_HINT_CHARS + 200, (ls.id, len(ls.hint_text))
        assert ls.hint_text or ls.output_text, ls.id  # 不能两侧都空


# ---------------------------------------------------------------------------
# 工具名对照（D5）
# ---------------------------------------------------------------------------


def test_extract_tool_names_normalizes_mcp_prefix_and_skips_meta() -> None:
    wind, ifind, free = extract_tool_names(
        "call mcp__ifind__get_stock_financials and wind_get_stock_fundamentals then get_financials；"
        "元工具 wind_query / ifind_list_apis 不该进对照表"
    )
    assert wind == ["wind_get_stock_fundamentals"]
    assert ifind == ["ifind_get_stock_financials"]
    assert free == ["get_financials"]


def test_env_var_names_are_not_mistaken_for_tools() -> None:
    """`WIND_API_KEY` / `IFIND_AUTH_TOKEN` 是环境变量，不是接口——大小写不敏感扫描会把它们翻成
    `wind_query(api_name="api_key")` 这种不存在的接口喂给 LLM（2026-09-08 实测踩到）。"""
    wind, ifind, _ = extract_tool_names(
        "配置 WIND_API_KEY（以 ak_ 开头）与 IFIND_AUTH_TOKEN；env var IFIND_DATA_SOURCE_MODE=ifind-only；"
        "通配写法 wind_get_fund_* 也不是真名"
    )
    assert wind == [] and ifind == []
    for ls in LIB:
        for old, _ in ls.tool_mapping:
            assert "api_key" not in old and "auth_token" not in old, (ls.id, old)
            assert not old.endswith("_"), (ls.id, old)


def test_source_families_come_from_prose_not_only_tool_names() -> None:
    """families 决定 `ifind_*` 元工具能不能进本轮工具面（它们**不在** META_TOOL_NAMES 里）——
    只看工具名的话 63 篇里仅 6 篇能用 iFind，而绝大多数正文的 Data Sources 都写着 Tier-1 同花顺。"""
    assert detect_source_families("Tier 0 — 万得 Wind；Tier 1 — 同花顺 iFind；Tier 2 — AkShare") == (
        "wind",
        "ifind",
        "free",
    )
    assert detect_source_families("纯清单，无数据源") == ()
    assert sum(1 for ls in LIB if "ifind_" in ls.tool_families) >= 50
    assert sum(1 for ls in LIB if "wind_" in ls.tool_families) >= 50
    ls = BY_ID["china-dcf"]
    assert "ifind_" in ls.tool_families and "wind_" in ls.tool_families
    assert not any(o.startswith("wind_") for o, _ in ls.tool_mapping)  # 正文没点名 wind_* 工具


def test_mapping_note_translates_to_gateway_meta_tools() -> None:
    note = BY_ID["china-earnings-analysis"].mapping_note
    assert "工具名对照" in note
    assert "ifind_query" in note and "只吃一个自然语言" in note
    assert "6 位裸代码" in note                      # 免费源代码格式（传后缀会 not found）
    assert "income / fina_indicator" in note        # get_financials 的 Tushare 官方等价
    wind_note = BY_ID["china-comps"].mapping_note
    if "wind_" in "".join(o for o, _ in BY_ID["china-comps"].tool_mapping):
        assert "wind_list_apis" in wind_note        # api_name 以实时目录为准，别照抄旧名


def test_wind_and_ifind_names_map_to_query_meta_tool() -> None:
    hits = [ls for ls in LIB if any(o.startswith("wind_") for o, _ in ls.tool_mapping)]
    assert hits, "语料里应有引用 wind_* 具名工具的技能"
    for ls in hits:
        for old, new in ls.tool_mapping:
            if old.startswith("wind_"):
                assert new.startswith('wind_query(api_name="')
            elif old.startswith("ifind_"):
                assert new.startswith("ifind_query(query=")


def test_tool_families_keep_meta_tools_for_wind_ifind_skills() -> None:
    """tool_select 的关键词打分只认 Tushare 接口名 → 依赖 Wind/iFind 的技能必须靠 families 恒保留。"""
    for ls in LIB:
        olds = [o for o, _ in ls.tool_mapping]
        if any(o.startswith("wind_") for o in olds):
            assert "wind_" in ls.tool_families, ls.id
        if any(o.startswith("ifind_") for o in olds):
            assert "ifind_" in ls.tool_families, ls.id
        # 免费源用**精确名**保留（子串会误伤一片同名前缀接口）
        for fam in ls.tool_families:
            assert fam in ("wind_", "ifind_") or not fam.endswith("_"), (ls.id, fam)


# ---------------------------------------------------------------------------
# 能力降级（D8）与纪律段（D9）
# ---------------------------------------------------------------------------


def test_files_limited_is_rule_derived_not_hand_maintained() -> None:
    """名单 = 规则输出（正文命中文件产物关键词 ≥3 次），避免手工名单随 upstream 漂移。"""
    flagged = sorted(ls.id for ls in LIB if ls.files_limited)
    assert flagged == sorted(
        ls.id for ls in LIB if len(_FILES_PAT.findall(ls.body)) >= _FILES_MIN_HITS
    )
    for wanted in ("china-xlsx-author", "china-pptx-author", "china-ppt-template-creator"):
        assert wanted in flagged, wanted


def test_capability_note_only_on_files_limited_skills() -> None:
    for ls in LIB:
        prompt = ls.system_prompt("BASE")
        assert (CAPABILITY_NOTE in prompt) is ls.files_limited, ls.id
    note = BY_ID["china-xlsx-author"].system_prompt("BASE")
    assert "不生成" in note and "不要声称已生成" in note


def test_every_imported_skill_carries_project_discipline() -> None:
    """`skill.system_prompt` 在 synthesizer 是**整体替换** synth_system（nodes.py:658）——
    不强制包纪律段就会丢掉三反编造约束与「正文不写来源名」。"""
    for ls in LIB:
        prompt = ls.system_prompt("BASE-SYSTEM")
        assert prompt.startswith("BASE-SYSTEM")
        assert LIBRARY_DISCIPLINE in prompt, ls.id
        assert "数据未接入" in prompt


def test_discipline_blocks_the_three_observed_leaks() -> None:
    """2026-09-08 真实链路实测到的三处越界，逐条锁住（回归用）：
    ①凭空落款「数据来源于万得 Wind」——那一轮实际只用了 iFind/Tushare/免费源，写上就是错的；
    ②正文提及「技能说明中……」把提示词内部结构漏给了用户；
    ③给出「增持」这类卖方评级标签（项目纪律只允许方向性判断）。"""
    assert "数据来源于" in LIBRARY_DISCIPLINE
    assert "万得" in LIBRARY_DISCIPLINE and "同花顺" in LIBRARY_DISCIPLINE
    assert "技能说明" in LIBRARY_DISCIPLINE
    assert "增持" in LIBRARY_DISCIPLINE and "方向性判断" in LIBRARY_DISCIPLINE


def test_tool_hint_mentions_skill_name_and_discovery() -> None:
    for ls in LIB:
        hint = ls.tool_hint()
        assert ls.name in hint, ls.id
        assert "list_apis" in hint


# ---------------------------------------------------------------------------
# RAG 名单（D7）与 router 清单体积（D6）
# ---------------------------------------------------------------------------


def test_rag_list_is_16_and_uses_factual_strategy() -> None:
    on = {ls.id for ls in LIB if ls.should_rag}
    assert on == set(RAG_SKILL_IDS) and len(on) == 16
    for ls in LIB:
        assert ls.strategy == ("factual" if ls.should_rag else "auto"), ls.id


def test_router_catalog_stays_small() -> None:
    """63 条原始 description 合计 20.7KB；router 每次提问都跑，必须用中文短描述压到 ~4KB。"""
    text = build_router_system(SKILLS)
    assert len(text) <= 6000, len(text)
    assert "china-dcf：A股DCF估值" in text
    assert "ai_supply_chain_tracker" in text
    # 原始 description 的英文触发词长句不该进 router
    assert "Triggers on" not in text


def test_registry_has_builtin_plus_library() -> None:
    assert len(SKILLS) == 1 + len(LIB) == 64
    assert SKILLS[0].id == "ai_supply_chain_tracker"
