"""各数据源的用法约定按「本轮工具清单里真的有什么」注入 system_prompt。

这不是文案测试，是**可用性**测试：三家的参数风格互不兼容（Tushare 结构化字段+带后缀代码 /
万得自然语言+Wind 后缀 / iFind 单个自然语言 query / 免费源 6 位裸代码），漏注入某段，
LLM 就会拿另一家的习惯传参而稳定取不到数——而且是静默失败，测试不锁住很难发现。
"""

from __future__ import annotations

from demomcp.agents.agent import base_system_for
from demomcp.config.settings import (
    FREE_SOURCE_USAGE_GUIDE,
    IFIND_USAGE_GUIDE,
    WIND_USAGE_GUIDE,
    Settings,
)
from demomcp.interfaces.types import ToolSpec


def _cfg() -> Settings:
    return Settings(_env_file=None, system_prompt="BASE")


def _specs(*names: str) -> list[ToolSpec]:
    return [ToolSpec(name=n, description="") for n in names]


# ---- 无源 / 纯 Tushare ----


def test_no_guides_when_only_tushare_tools() -> None:
    """只有 Tushare 原生接口时不追加任何额外用法段（省 token，也避免讲用不上的源）。"""
    system = base_system_for(_cfg(), _specs("daily", "stock_basic", "daily_basic", "forecast"))
    assert system == "BASE"


def test_no_guides_on_empty_tool_list() -> None:
    assert base_system_for(_cfg(), []) == "BASE"


# ---- 单源注入 ----


def test_wind_guide_injected_only_on_wind_tools() -> None:
    system = base_system_for(_cfg(), _specs("wind_query", "daily"))
    assert WIND_USAGE_GUIDE in system
    assert IFIND_USAGE_GUIDE not in system
    assert FREE_SOURCE_USAGE_GUIDE not in system


def test_ifind_guide_injected_only_on_ifind_tools() -> None:
    system = base_system_for(_cfg(), _specs("ifind_query", "ifind_list_apis"))
    assert IFIND_USAGE_GUIDE in system
    assert WIND_USAGE_GUIDE not in system
    assert FREE_SOURCE_USAGE_GUIDE not in system


def test_free_source_guide_injected_on_unprefixed_sentinels() -> None:
    """免费源工具名没有前缀，靠哨兵名识别。"""
    system = base_system_for(_cfg(), _specs("get_quote", "get_market_overview"))
    assert FREE_SOURCE_USAGE_GUIDE in system
    assert WIND_USAGE_GUIDE not in system


def test_news_only_free_source_still_triggers_guide() -> None:
    """只开了新闻源（没开 AkShare）时也要讲——它同样是 6 位裸代码。"""
    system = base_system_for(_cfg(), _specs("get_stock_news", "get_market_headlines"))
    assert FREE_SOURCE_USAGE_GUIDE in system


# ---- 多源共存 ----


def test_all_guides_coexist_when_all_sources_enabled() -> None:
    system = base_system_for(
        _cfg(), _specs("daily", "wind_query", "ifind_query", "get_market_overview")
    )
    for guide in (WIND_USAGE_GUIDE, IFIND_USAGE_GUIDE, FREE_SOURCE_USAGE_GUIDE):
        assert guide in system
    assert system.startswith("BASE")


def test_guide_is_appended_once_per_source() -> None:
    """同一源有多个工具时不重复追加（否则 system prompt 会成倍膨胀）。"""
    system = base_system_for(_cfg(), _specs("wind_query", "wind_list_apis", "wind_get_api_info"))
    assert system.count(WIND_USAGE_GUIDE) == 1


# ---- 内容契约：这几条是「不写就一定取不到数」的关键约定 ----


def test_ifind_guide_states_natural_language_query_contract() -> None:
    """iFind 只吃一句自然语言 query，且要明确否掉 Tushare 风格的结构化字段。"""
    assert "query" in IFIND_USAGE_GUIDE
    assert "自然语言" in IFIND_USAGE_GUIDE
    assert "ts_code" in IFIND_USAGE_GUIDE  # 明确告诉模型别传这个


def test_free_source_guide_states_bare_six_digit_code_contract() -> None:
    """免费源要 600519 而不是 600519.SH——这条不写，get_quote 会稳定 not found。"""
    assert "600519.SH" in FREE_SOURCE_USAGE_GUIDE
    assert "6 位裸代码" in FREE_SOURCE_USAGE_GUIDE


def test_free_source_guide_points_at_news_gap() -> None:
    """Tushare 官方 news 无权限，需要新闻时应引导到免费源。"""
    assert "get_stock_news" in FREE_SOURCE_USAGE_GUIDE
    assert "news" in FREE_SOURCE_USAGE_GUIDE


def test_guides_warn_against_fabricating_numbers() -> None:
    """三段都必须带「如实转述、不要编造数字」——取数失败时的行为约定。"""
    for guide in (WIND_USAGE_GUIDE, IFIND_USAGE_GUIDE, FREE_SOURCE_USAGE_GUIDE):
        assert "编造" in guide
