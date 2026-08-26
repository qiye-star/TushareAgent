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
