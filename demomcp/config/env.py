"""路径（demo-mcp 自带，零父路径）。

PROJECT_ROOT 指向 demo-mcp 项目根（本文件上两级）。MCP 服务器（mcp_server/server.py）是**独立 HTTP 服务**，
由应用经 `demomcp.config.settings.tushare_mcp_url` 连接，本项目不再以子进程拉起，因此这里也不再构造 stdio 参数。
"""

from __future__ import annotations

from pathlib import Path

# demomcp/config/env.py -> parents[0]=config, [1]=demomcp, [2]=demo-mcp
PROJECT_ROOT = Path(__file__).resolve().parents[2]
