"""demo-mcp 离线测试共享夹具：确保 demomcp 可导入，并提供与仓库根 .env 解耦的 Settings 工厂。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from demomcp.config.settings import Settings

DEMO_MCP_ROOT = Path(__file__).resolve().parents[1]
if str(DEMO_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(DEMO_MCP_ROOT))

_ENV_KEYS = [
    "DS_API_KEY", "DS_BASE_URL", "DS_MODEL", "DS_MAX_TOKENS", "DS_STREAMING",
    "DEMO_SYSTEM_PROMPT", "DEMO_MAX_ITERATIONS",
    "TUSHARE_PROXY_URL", "TUSHARE_API_KEY", "TUSHARE_PROXY_TIMEOUT",
    "MCP_PYTHON", "MCP_SERVER_PATH", "MCP_ARGS",
]


@pytest.fixture
def make_settings(monkeypatch):
    """返回一个 Settings 工厂：屏蔽仓库根 .env 与上述环境变量，测试只依赖默认值/显式传入值。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    def _make(**kwargs):
        # _env_file=None 屏蔽仓库根 .env；pyright 不识别 pydantic-settings 的这个构造形参
        return Settings(_env_file=None, **kwargs)  # type: ignore

    return _make
