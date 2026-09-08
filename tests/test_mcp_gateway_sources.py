"""网关源注册：配置齐备与否决定某源是否注册（不留永远连不上的开关项），config_problems 照实报缺。

全部用 `_env_file=None` 构造 GatewaySettings —— 默认会读真实的 mcp_gateway/.env，
开发机上那份带真凭证，不隔离的话断言会跟着本机配置漂。
"""

from __future__ import annotations

import pytest

from mcp_gateway.config import GatewaySettings
from mcp_gateway.sources import build_sources


def _settings(**kwargs) -> GatewaySettings:
    """构造一个只认显式入参的 Settings（屏蔽 .env 与进程 env 的干扰）。"""
    return GatewaySettings(_env_file=None, **kwargs)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    """清掉可能从外部漏进来的源配置环境变量，让每个用例只受自己的入参影响。"""
    for name in (
        "TUSHARE_MCP_URL",
        "WIND_API_KEY",
        "WIND_ENABLED",
        "IFIND_AUTH_TOKEN",
        "IFIND_ENABLED",
        "IFIND_CONCURRENCY",
        "AKSHARE_MCP_URL",
        "CHINA_NEWS_MCP_URL",
    ):
        monkeypatch.delenv(name, raising=False)


# ---- configured 判定 ----


def test_ifind_configured_requires_token_and_enabled() -> None:
    assert _settings(ifind_auth_token="tok", ifind_enabled=True).ifind_configured is True
    # 有 token 但显式关掉 → 不注册
    assert _settings(ifind_auth_token="tok", ifind_enabled=False).ifind_configured is False
    # 开着但没 token → 不注册（而不是注册一个必然 401 的源）
    assert _settings(ifind_auth_token="", ifind_enabled=True).ifind_configured is False


def test_free_sources_configured_by_url_only() -> None:
    """免费源没有凭证概念，配了 URL 就算配好。"""
    s = _settings(akshare_mcp_url="http://127.0.0.1:8000/mcp")
    assert s.akshare_configured is True
    assert s.china_news_configured is False


def test_any_source_configured_counts_new_sources() -> None:
    """只配 iFind（或只配免费源）也算「有源」——漏了这条会把这些情况误报成空壳网关。"""
    assert _settings().any_source_configured is False
    assert _settings(ifind_auth_token="tok").any_source_configured is True
    assert _settings(akshare_mcp_url="http://x/mcp").any_source_configured is True
    assert _settings(china_news_mcp_url="http://x/mcp").any_source_configured is True


# ---- config_problems ----


def test_config_problems_flags_ifind_enabled_without_token() -> None:
    problems = _settings(ifind_enabled=True, ifind_auth_token="").config_problems()
    assert any("IFIND_AUTH_TOKEN" in p for p in problems)


def test_config_problems_silent_on_ifind_when_explicitly_disabled() -> None:
    problems = _settings(ifind_enabled=False, tushare_mcp_url="https://x/mcp/?token=t").config_problems()
    assert not any("IFIND" in p for p in problems)


def test_config_problems_flags_bad_ifind_concurrency() -> None:
    problems = _settings(ifind_auth_token="tok", ifind_concurrency=0).config_problems()
    assert any("IFIND_CONCURRENCY" in p for p in problems)


def test_config_problems_no_empty_shell_complaint_when_only_ifind() -> None:
    """只配了 iFind 时不该再说「没有任何可用上游源」。"""
    problems = _settings(ifind_auth_token="tok").config_problems()
    assert not any("没有任何可用上游源" in p for p in problems)


def test_config_problems_complains_when_nothing_configured() -> None:
    problems = _settings().config_problems()
    assert any("没有任何可用上游源" in p for p in problems)


# ---- build_sources ----


def test_build_sources_registers_nothing_when_unconfigured() -> None:
    assert build_sources(_settings(wind_enabled=False, ifind_enabled=False)) == []


def test_build_sources_registers_all_five_when_configured() -> None:
    sources = build_sources(
        _settings(
            tushare_mcp_url="https://api.tushare.pro/mcp/?token=t",
            wind_api_key="ak_x",
            ifind_auth_token="tok",
            akshare_mcp_url="http://127.0.0.1:8000/mcp",
            china_news_mcp_url="http://127.0.0.1:8001/mcp",
        )
    )
    assert [s.id for s in sources] == ["tushare", "wind", "ifind", "akshare", "china_news"]


def test_build_sources_skips_unconfigured_individually() -> None:
    """只配 iFind + 免费新闻：其余三个源完全不出现（/admin/sources 里也就不会有它们）。"""
    sources = build_sources(
        _settings(wind_enabled=False, ifind_auth_token="tok", china_news_mcp_url="http://x/mcp")
    )
    assert [s.id for s in sources] == ["ifind", "china_news"]


def test_build_sources_display_names_are_human_readable() -> None:
    sources = build_sources(_settings(ifind_auth_token="tok", akshare_mcp_url="http://x/mcp"))
    names = {s.id: s.display_name for s in sources}
    assert names == {"ifind": "同花顺 iFind", "akshare": "AkShare（免费）"}
