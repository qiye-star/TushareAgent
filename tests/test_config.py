"""配置层离线测试：Settings 读取 + 项目根 + MCP URL + DB URL。"""

from __future__ import annotations

from demomcp.config.env import PROJECT_ROOT
from demomcp.config.settings import DEFAULT_SYSTEM_PROMPT


def test_settings_defaults_offline(make_settings) -> None:
    s = make_settings()
    assert s.ds_base_url == "https://api.deepseek.com"
    assert s.ds_model == "deepseek-chat"
    assert s.ds_max_tokens == 8192
    assert s.ds_streaming is True
    assert s.max_iterations == 10
    assert s.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert s.mcp_timeout == 30.0
    assert s.mcp_retries == 2
    assert s.mcp_keepalive_interval == 45.0
    assert s.tool_pool_hot_start is True
    assert s.is_configured is False


def test_mcp_keepalive_and_hot_start_from_env(make_settings, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_MCP_KEEPALIVE", "0")
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")
    s = make_settings()
    assert s.mcp_keepalive_interval == 0.0  # 0=关闭保活
    assert s.tool_pool_hot_start is False  # 关闭热启动

    monkeypatch.setenv("DEMO_MCP_KEEPALIVE", "60")
    monkeypatch.setenv("TOOL_POOL_HOT_START", "true")
    s2 = make_settings()
    assert s2.mcp_keepalive_interval == 60.0
    assert s2.tool_pool_hot_start is True


def test_settings_from_env(make_settings, monkeypatch) -> None:
    monkeypatch.setenv("DS_API_KEY", "sk-x")
    monkeypatch.setenv("DS_BASE_URL", "http://gw")
    monkeypatch.setenv("DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEMO_MAX_ITERATIONS", "3")
    monkeypatch.setenv("DEMO_SYSTEM_PROMPT", "X")
    s = make_settings()
    assert s.ds_api_key == "sk-x"
    assert s.ds_base_url == "http://gw"
    assert s.ds_model == "deepseek-v4-flash"
    assert s.max_iterations == 3
    assert s.system_prompt == "X"
    assert s.is_configured is True


def test_project_root_is_repo_root() -> None:
    # PROJECT_ROOT 由 demomcp/config/env.py 的 parents[2] 推导，应指向仓库根（不依赖所在目录名）
    assert PROJECT_ROOT.is_absolute()
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert (PROJECT_ROOT / "demomcp").is_dir()


def test_effective_database_url_fallback(make_settings) -> None:
    s = make_settings()
    assert s.effective_database_url == f"sqlite+aiosqlite:///{PROJECT_ROOT / 'demo.db'}"


def test_effective_database_url_override(make_settings) -> None:
    s = make_settings(demo_database_url="sqlite+aiosqlite:///tmp/chat.db")
    assert s.effective_database_url == "sqlite+aiosqlite:///tmp/chat.db"


def test_mcp_gateway_url_default_points_at_local_gateway(make_settings) -> None:
    """取数唯一入口是网关；上游源（TUSHARE_MCP_URL/WIND_*）已迁往 mcp_gateway/.env，这里不该再有。"""
    s = make_settings()
    assert s.mcp_gateway_url == "http://127.0.0.1:8766/mcp"
    for gone in ("tushare_mcp_url", "wind_api_key", "wind_enabled", "wind_configured"):
        assert not hasattr(s, gone), f"{gone} 应已随网关化移除（配置只在 mcp_gateway/.env 一处）"


def test_mcp_gateway_url_from_env(make_settings, monkeypatch) -> None:
    monkeypatch.setenv("MCP_GATEWAY_URL", "http://gw:9000/mcp")
    assert make_settings().mcp_gateway_url == "http://gw:9000/mcp"


def test_wind_usage_guide_injected_only_when_wind_tools_present(make_settings) -> None:
    """万得用法约定现在由「本轮工具清单里有没有 wind_ 前缀工具」决定，不再看 demomcp 的配置。"""
    from demomcp.agents.agent import base_system_for
    from demomcp.interfaces.types import ToolSpec

    cfg = make_settings()
    base = cfg.system_prompt
    assert base_system_for(cfg, []) == base  # 无工具：逐字节一致
    assert base_system_for(cfg, [ToolSpec(name="daily")]) == base  # 只有 Tushare 工具：不提万得

    prompt = base_system_for(cfg, [ToolSpec(name="daily"), ToolSpec(name="wind_list_apis")])
    assert len(prompt) > len(base)
    assert "Wind 格式" in prompt
    assert "自然语言" in prompt
    assert "wind_list_apis" in prompt and "wind_get_api_info" in prompt and "wind_query" in prompt
