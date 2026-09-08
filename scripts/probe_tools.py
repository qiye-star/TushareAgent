"""按需探测各数据接口可用性，写 data/tool_catalog.json（之后 agent 自动剔除 blocked/down）。

用法：
    uv run scripts/probe_tools.py
经 MCP 网关取工具面（`MCP_GATEWAY_URL`，默认 http://127.0.0.1:8766/mcp），所以**要先把网关起起来**；
探到的正是 agent 实际看到的那批工具（网关里被关掉的源不会出现）。上游源的 token/key 配在
mcp_gateway/.env，本脚本不需要。默认只探测非 meta 工具（list_apis/get_api_info/query/stock_basic 除外）。
"""

from __future__ import annotations

import asyncio

from demomcp.config.settings import Settings
from demomcp.providers.tools.curate import probe_availability, save_catalog
from demomcp.providers.tools.mcp import mcp_tool_provider


async def main() -> int:
    s = Settings()
    async with mcp_tool_provider(
        s.mcp_gateway_url, timeout=s.mcp_timeout, retries=s.mcp_retries
    ) as tools:
        specs = await tools.list_tools()
        print(f"[probe] 发现工具 {len(specs)} 个，开始探测可用性（并发 {s.tool_probe_concurrency}）…")
        catalog = await probe_availability(tools, specs, concurrency=s.tool_probe_concurrency)
        save_catalog(catalog, s.tool_probe_cache_path)
        counts: dict[str, int] = {}
        for info in catalog.values():
            status = info.get("status", "usable")
            counts[status] = counts.get(status, 0) + 1
        shown = {k: v for k, v in counts.items() if v}
        print(f"[probe] 完成：{shown}")
        print(f"[probe] 缓存 → {s.tool_probe_cache_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
