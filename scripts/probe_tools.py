"""按需探测各数据接口可用性，写 data/tool_catalog.json（之后 agent 自动剔除 blocked/down）。

用法：
    uv run scripts/probe_tools.py
需真 .env：TUSHARE_MCP_URL（官方 MCP token）；可选 WIND_API_KEY。默认只探测非 meta 工具（list_apis/get_api_info/query/stock_basic 除外）。
"""

from __future__ import annotations

import asyncio

from demomcp.config.settings import Settings
from demomcp.providers.tools.curate import probe_availability, save_catalog
from demomcp.providers.tools.wind import agent_tool_provider


async def main() -> int:
    s = Settings()
    async with agent_tool_provider(s) as tools:
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
