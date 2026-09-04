"""dynamic tool curation：select_tools 确定性 per-query 过滤测试（不联网，纯函数）。"""

from __future__ import annotations

from demomcp.graph.tool_select import select_tools
from demomcp.interfaces.types import ToolSpec

PICTURE_SPECS = [
    ToolSpec("list_apis", "列出可用接口"),
    ToolSpec("get_api_info", "查看某接口详情"),
    ToolSpec("query", "通用取数"),
    ToolSpec("stock_basic", "上市公司基本信息"),
    ToolSpec("daily", "日线行情"),
    ToolSpec("adj_factor", "复权因子"),
    ToolSpec("income", "利润表/营收"),
    ToolSpec("fina_indicator", "财务指标"),
    ToolSpec("index_daily", "指数日线"),
    ToolSpec("bond_basic", "债券"),
    ToolSpec("top_list", "龙虎榜"),
]


def assert_meta_present(names: list[str]) -> None:
    for m in ("list_apis", "get_api_info", "query", "stock_basic"):
        assert m in names, f"meta {m} 应恒在"


async def test_select_tools_keeps_meta_and_relevant_finance() -> None:
    names = [s.name for s in select_tools(PICTURE_SPECS, "贵州茅台 营收 毛利率 财务")]
    assert_meta_present(names)
    assert "income" in names and "fina_indicator" in names
    assert "bond_basic" not in names  # 债券与财务不相关


async def test_select_tools_respects_max_revealed() -> None:
    big = list(PICTURE_SPECS) + [ToolSpec(f"daily{i}", f"日线{i}") for i in range(30)]
    names = [s.name for s in select_tools(big, "行情", max_revealed=12)]
    assert_meta_present(names)
    revealed = [n for n in names if n.startswith("daily")]
    assert len(revealed) <= 12
    assert "daily" in names  # 「行情」topic → daily 族
    assert "daily29" not in names  # 超过 cap 的未揭示


async def test_select_tools_no_meta_zero_hit_falls_back_to_all() -> None:
    # 无 meta + 零命中 → 回退全量（不裁空）
    names = [s.name for s in select_tools([ToolSpec("a", "x"), ToolSpec("b", "y")], "天气如何")]
    assert names == ["a", "b"]


async def test_select_tools_meta_always_disabled_drops_meta() -> None:
    names = [s.name for s in select_tools(PICTURE_SPECS, "营收", meta=frozenset())]
    # 无 meta 时凭相关性仍能选到 income
    assert "income" in names


async def test_select_tools_catalog_excludes_blocked_and_down() -> None:
    catalog = {
        "income": {"status": "blocked", "msg": "积分不足"},
        "fina_indicator": {"status": "down", "msg": "接口下线"},
    }
    names = [s.name for s in select_tools(PICTURE_SPECS, "营收 毛利率", catalog=catalog)]
    assert_meta_present(names)
    assert "income" not in names and "fina_indicator" not in names  # 被剔除


async def test_select_tools_skill_tools_kept_and_not_capped() -> None:
    """skill 声明的接口（即使 query 零命中）恒保留，且不进 max_revealed 限额。"""
    big = list(PICTURE_SPECS) + [ToolSpec(f"daily{i}", f"日线{i}") for i in range(20)]
    names = [s.name for s in select_tools(
        big, "生成AI算力产业链今日跟踪快报", max_revealed=4,
        skill_tools=frozenset({"daily", "forecast", "announcement"}),
    )]
    # meta + 技能接口恒在（不受 max_revealed=4 限制）
    assert "daily" in names and "get_api_info" in names
    # 与「行情」无关的 query 下，daily/daily_basic 靠 skill_tools 保留而非相关性
    assert "daily" in names


async def test_select_tools_topic_board_moneyflow_forecast() -> None:
    """扩充后的话题词：板块/资金流/业绩预告/公告 → 命中对应接口。"""
    specs = list(PICTURE_SPECS) + [
        ToolSpec("concept", "板块/概念"), ToolSpec("moneyflow", "资金流向"), ToolSpec("forecast", "业绩预告"),
    ]
    names = [s.name for s in select_tools(specs, "某板块今日资金流与业绩预告")]
    assert "query" in names  # meta
    assert "concept" in names and "moneyflow" in names and "forecast" in names


async def test_domain_alias_shared_consistency() -> None:
    """_domain_terms 与 nodes._domain_expand 都用 DOMAIN_ALIASES，乘用车展开一致。"""
    from demomcp.graph.nodes import _domain_expand
    from demomcp.graph.tool_select import _domain_terms
    from demomcp.rag.query_build import DOMAIN_ALIASES

    assert DOMAIN_ALIASES["乘用车"] == ("汽车", "新能源汽车", "整车")
    assert _domain_terms("乘用车业务") == _domain_expand("乘用车业务") == ["汽车", "新能源汽车", "整车"]
