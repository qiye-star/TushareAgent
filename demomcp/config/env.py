"""路径与 MCP stdio 参数（demo-mcp 自带，零父路径）。

PROJECT_ROOT 指向 demo-mcp 项目根（本文件上两级）；MCP 服务器用**内置**的
demo-mcp/mcp_server/server.py（默认由 build_stdio_params 回落），
MCP_PYTHON 留空则用当前解释器（demo-mcp venv / 容器解释器）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.client.stdio import StdioServerParameters

# demomcp/config/env.py -> parents[0]=config, [1]=demomcp, [2]=demo-mcp
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_stdio_params(
    *,
    tushare_proxy_url: str,
    tushare_api_key: str,
    tushare_proxy_timeout: float = 20.0,
    mcp_python: str = "",
    mcp_server_path: str = "",
    mcp_args: list[str] | None = None,
) -> StdioServerParameters:
    """构造成 MCP 子进程的 StdioServerParameters（纯配置驱动）。

    - MCP_SERVER_PATH 留空 → 用内置的 demo-mcp/mcp_server/server.py（自包含）。
    - MCP_PYTHON 留空 → 用当前解释器（demo-mcp venv，自带 mcp+httpx）。
    - 把 TUSHARE_* 透传给子进程（其 os.environ 读取，名字必须一致）。
    """
    server = Path(mcp_server_path) if mcp_server_path else PROJECT_ROOT / "mcp_server" / "server.py"
    if not server.exists():
        raise RuntimeError(f"MCP 服务器脚本不存在：{server}（参考 demo-mcp/mcp_server/server.py）")
    python = Path(mcp_python) if mcp_python else Path(sys.executable)
    env = {
        **os.environ,
        "TUSHARE_PROXY_URL": tushare_proxy_url,
        "TUSHARE_API_KEY": tushare_api_key,
        "TUSHARE_PROXY_TIMEOUT": str(tushare_proxy_timeout),
    }
    return StdioServerParameters(command=str(python), args=[str(server), *(mcp_args or [])], env=env)
