"""真实端到端冒烟（可选）：需代理在跑 + 真 TUSHARE_API_KEY + 真 DS_API_KEY。

用法（demo-mcp 自带 .venv，先 uv sync）：
    python scripts/smoke_e2e.py      # 或 demo-mcp/.venv 解释器直接跑
"""

from __future__ import annotations

import asyncio

from demomcp.agents.agent import Agent
from demomcp.config.env import build_stdio_params
from demomcp.config.settings import Settings
from demomcp.providers.llm.deepseek import DeepSeekLLMClient
from demomcp.providers.tools.mcp import mcp_tool_provider


async def main() -> int:
    settings = Settings()
    if not settings.ds_api_key:
        raise SystemExit("DS_API_KEY 未配置（demo-mcp/.env）")
    if not settings.tushare_api_key:
        raise SystemExit("TUSHARE_API_KEY 未配置（demo-mcp/.env）")

    llm = DeepSeekLLMClient(
        api_key=settings.ds_api_key,
        base_url=settings.ds_base_url or None,
        model=settings.ds_model or None,
    )
    params = build_stdio_params(
        tushare_proxy_url=settings.tushare_proxy_url,
        tushare_api_key=settings.tushare_api_key,
        tushare_proxy_timeout=settings.tushare_proxy_timeout,
        mcp_python=settings.mcp_python,
        mcp_server_path=settings.mcp_server_path,
        mcp_args=settings.mcp_args,
    )
    async with mcp_tool_provider(params) as tools:
        agent = Agent(llm=llm, tools=tools, config=settings)
        result = await agent.run("列出 5 个与复权相关的接口，并查询 adj_factor 的最近数据")
        print(f"\n[stop: {result.stopped_reason}]")
        print(result.final_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
