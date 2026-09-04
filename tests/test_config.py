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
    assert s.is_configured is False


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


def test_tushare_mcp_url_default(make_settings) -> None:
    s = make_settings()
    assert s.tushare_mcp_url == "https://api.tushare.pro/mcp/"


def test_tushare_mcp_url_from_env(make_settings, monkeypatch) -> None:
    monkeypatch.setenv("TUSHARE_MCP_URL", "http://10.0.0.1:9000/mcp")
    s = make_settings()
    assert s.tushare_mcp_url == "http://10.0.0.1:9000/mcp"


def test_wind_defaults_offline(make_settings) -> None:
    s = make_settings()
    assert s.wind_api_key == ""
    assert s.wind_enabled is True
    assert s.wind_configured is False


def test_wind_configured_gate(make_settings) -> None:
    # key 为空 → 未装配
    assert make_settings().wind_configured is False
    # 有 key → 装配
    assert make_settings(wind_api_key="ak_x").wind_configured is True
    # key + WIND_ENABLED=false → 未装配
    assert make_settings(wind_api_key="ak_x", wind_enabled=False).wind_configured is False


def test_effective_system_prompt_mentions_wind_only_when_configured(make_settings) -> None:
    base = make_settings().system_prompt
    assert make_settings().effective_system_prompt == base  # 未装配：逐字节一致
    prompt = make_settings(wind_api_key="ak_x").effective_system_prompt
    assert "wind_" in prompt
    assert len(prompt) > len(base)


def test_effective_system_prompt_injects_wind_usage_guide(make_settings) -> None:
    prompt = make_settings(wind_api_key="ak_x").effective_system_prompt
    # WIND_USAGE_GUIDE 的关键约定被注入
    assert "Wind 格式" in prompt
    assert "自然语言" in prompt
    assert "wind_search_stocks" in prompt
    assert "不要用" in prompt or "不要**用" in prompt
