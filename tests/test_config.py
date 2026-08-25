"""配置层离线测试：Settings 读取 + 项目根 + MCP stdio 参数 + DB URL。"""

from __future__ import annotations

import sys
from pathlib import Path

from demomcp.config.env import PROJECT_ROOT, build_stdio_params
from demomcp.config.settings import DEFAULT_SYSTEM_PROMPT


def test_settings_defaults_offline(make_settings) -> None:
    s = make_settings()
    assert s.ds_base_url == "https://api.deepseek.com"
    assert s.ds_model == "deepseek-chat"
    assert s.ds_max_tokens == 8192
    assert s.ds_streaming is True
    assert s.max_iterations == 10
    assert s.system_prompt == DEFAULT_SYSTEM_PROMPT
    assert s.tushare_proxy_url == "http://127.0.0.1:8000"
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


def test_project_root_is_demo_mcp() -> None:
    assert PROJECT_ROOT.is_absolute()
    assert PROJECT_ROOT.name == "demo-mcp"


def test_effective_database_url_fallback(make_settings) -> None:
    s = make_settings()
    assert s.effective_database_url == f"sqlite+aiosqlite:///{PROJECT_ROOT / 'demo.db'}"


def test_effective_database_url_override(make_settings) -> None:
    s = make_settings(demo_database_url="sqlite+aiosqlite:///tmp/chat.db")
    assert s.effective_database_url == "sqlite+aiosqlite:///tmp/chat.db"


def test_build_stdio_params_defaults_to_builtin_server() -> None:
    p = build_stdio_params(tushare_proxy_url="http://127.0.0.1:8000", tushare_api_key="k123")
    assert p.args is not None
    # 未指定 MCP_SERVER_PATH → 内置的 demo-mcp/mcp_server/server.py（自包含，无父路径）
    assert Path(p.args[0]) == PROJECT_ROOT / "mcp_server" / "server.py"
    assert Path(p.args[0]).exists()
    assert p.command == sys.executable  # 未指定 MCP_PYTHON → 当前解释器
    assert p.env is not None
    assert p.env["TUSHARE_API_KEY"] == "k123"
    assert p.env["TUSHARE_PROXY_URL"] == "http://127.0.0.1:8000"


def test_build_stdio_params_config_driven_override() -> None:
    p = build_stdio_params(
        tushare_proxy_url="http://127.0.0.1:8000",
        tushare_api_key="k123",
        tushare_proxy_timeout=5.0,
        mcp_server_path=str(PROJECT_ROOT / "mcp_server" / "server.py"),
    )
    assert p.args is not None
    assert Path(p.args[0]) == PROJECT_ROOT / "mcp_server" / "server.py"
    assert p.env is not None
    assert p.env["TUSHARE_PROXY_TIMEOUT"] == "5.0"
