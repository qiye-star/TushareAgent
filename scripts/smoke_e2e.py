"""真实端到端冒烟（可选）：需真 DS_API_KEY + .env 的 TUSHARE_MCP_URL 指向官方 MCP 且 token 有效。

用法（demo-mcp 自带 .venv，先 uv sync）：
    python scripts/smoke_e2e.py      # 或 demo-mcp/.venv 解释器直接跑
"""

from __future__ import annotations

import asyncio

from demomcp.agents.agent import Agent
from demomcp.config.settings import Settings
from demomcp.providers.llm.deepseek import DeepSeekLLMClient
from demomcp.providers.tools.mcp import mcp_tool_provider


async def main() -> int:
    settings = Settings()
    if not settings.ds_api_key:
        raise SystemExit("DS_API_KEY 未配置（demo-mcp/.env）")

    llm = DeepSeekLLMClient(
        api_key=settings.ds_api_key,
        base_url=settings.ds_base_url or None,
        model=settings.ds_model or None,
    )
    async with mcp_tool_provider(
        settings.tushare_mcp_url, timeout=settings.mcp_timeout, retries=settings.mcp_retries
    ) as tools:
        agent = Agent(llm=llm, tools=tools, config=settings)
        result = await agent.run("列出 5 个与复权相关的接口，并查询 adj_factor 的最近数据")
        print(f"\n[stop: {result.stopped_reason}]")
        print(result.final_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
