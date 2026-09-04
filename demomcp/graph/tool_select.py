"""确定性工具目录：per-query 从全量 ToolSpec 里挑相关子集，meta 发现工具恒保留。

只收窄 LLM 能选的（`llm.chat(tools=...)`），不 gate 执行（`tools.call_tool` 全量分发）。
词汇来源复用 `rag.query_build.extract_concepts`（`FIN_TERMS`）+ 共享 `rag.query_build.DOMAIN_ALIASES`
（避免 import `graph.nodes`（nodes 会 import 本模块，会造成环）；口语别名表已下沉到 query_build）。
"""

from __future__ import annotations

from typing import Any

from demomcp.interfaces.types import META_TOOL_NAMES, ToolSpec
from demomcp.rag.query_build import DOMAIN_ALIASES, extract_concepts

# 话题词 → 相关接口名子串（确定性命中；接口名是英文如 income/fina_indicator/daily）。
_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "行情": ("daily",),
    "价格": ("daily", "adj_factor"),
    "涨跌": ("daily", "adj_factor"),
    "分时": ("daily", "daily_basic"),
    "区间": ("daily", "adj_factor"),
    "成交量": ("daily", "moneyflow"),
    "市值": ("daily_basic",),
    "市盈": ("daily_basic",),
    "市净": ("daily_basic",),
    "估值": ("daily_basic",),
    "股息": ("daily_basic",),
    "财务": ("income", "fina_indicator", "balancesheet", "cashflow"),
    "营收": ("income",),
    "收入": ("income",),
    "利润": ("income", "fina_indicator"),
    "毛利率": ("fina_indicator",),
    "净利率": ("fina_indicator",),
    "净利": ("income", "fina_indicator"),
    "资产负债": ("fina_indicator", "balancesheet"),
    "现金流": ("cashflow",),
    "研发": ("income", "fina_indicator"),
    "股东": ("top10_holders", "top10_floatholders", "share_holder"),
    "公司": ("stock_basic",),
    "基本信息": ("stock_basic",),
    "指数": ("index_daily", "index_basic"),
    "基金": ("fund_basic", "fund_nav"),
    "债券": ("bond_basic",),
    "宏观": ("cn_m", "shibor", "money_supply"),
    "北向": ("moneyflow_hsgt", "hk_hold"),
    "融资": ("margin", "margin_detail"),
    "龙虎榜": ("top_list", "top_inst"),
    # 板块/资金流/公告/业绩预告/新闻/成分（快报、产业链跟踪类）——接口名子串需与真实 tool_defs 对齐
    "板块": ("concept", "ths_index", "sector"),
    "概念": ("concept", "concept_cons"),
    "产业链": ("concept", "ths_index"),
    "资金流": ("moneyflow", "moneyflow_hsgt"),
    "公告": ("announcement",),
    "业绩预告": ("forecast",),
    "新闻": ("news",),
    "成分": ("concept_cons", "index_member"),
    "快报": ("daily", "daily_basic", "forecast", "announcement"),
}


def _norm(s: str) -> str:
    """归一化匹配串：小写、去空白。"""
    return s.strip().lower().replace(" ", "")


def _domain_terms(query: str) -> list[str]:
    """命中触发词的口语 → 年报/金融措辞（共享 DOMAIN_ALIASES）。"""
    out: list[str] = []
    for trigger, terms in DOMAIN_ALIASES.items():
        if trigger in query:
            for t in terms:
                if t not in out:
                    out.append(t)
    return out


def _score(spec: ToolSpec, query: str, vocab: set[str]) -> float:
    """相关性分：名字命中 > 描述命中 > 领域文义命中。"""
    name = _norm(spec.name)
    desc = _norm(spec.description)
    score = 0.0
    for v in vocab:
        if v and v in name:
            score += 3.0
        elif v and v in desc:
            score += 1.0
    for kw, families in _TOPIC_KEYWORDS.items():
        if kw in query:
            for fam in families:
                if _norm(fam) in name:
                    score += 2.0
    return score


def select_tools(
    specs: list[ToolSpec],
    query: str,
    *,
    max_revealed: int = 12,
    meta: frozenset[str] = META_TOOL_NAMES,
    catalog: dict[str, Any] | None = None,
    skill_tools: frozenset[str] = frozenset(),
) -> list[ToolSpec]:
    """挑出本轮该喂给 LLM 的子集：meta + skill_tools 恒在 + 相关性 top-K；可用性 catalog 剔除 blocked/down。

    - skill_tools：skill 声明的接口名子串（如快报→daily/forecast/announcement），**恒保留**、不进 max_revealed 限额。
    - 若既无 meta 也无相关候选（零命中），回退到原 `specs` 不做减法，避免 LLM 无工具可选。
    """
    query = query or ""
    vocab = {_norm(t) for t in [*extract_concepts(query), *_domain_terms(query)] if t}

    blocked: set[str] = set()
    if catalog:
        for name, info in catalog.items():
            if isinstance(info, dict) and info.get("status") in ("blocked", "down"):
                blocked.add(str(name))

    meta_specs: list[ToolSpec] = []
    ranked: list[tuple[float, ToolSpec]] = []
    for spec in specs:
        if spec.name in meta:
            meta_specs.append(spec)
            continue
        if spec.name in blocked:
            continue
        if any(sf in _norm(spec.name) for sf in skill_tools):
            meta_specs.append(spec)  # skill 所需接口恒保留
            continue
        score = _score(spec, query, vocab)
        if score > 0:
            ranked.append((score, spec))

    ranked.sort(key=lambda t: (-t[0], t[1].name))
    kept = meta_specs + [spec for _, spec in ranked[:max_revealed]]
    return kept if kept else list(specs)
