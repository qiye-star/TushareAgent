"""China News MCP Server —— 免费财经新闻源（东方财富，经开源 akshare 库）。

**独立进程、独立依赖**，与 `akshare_server.py` 同构：网关经 `CHINA_NEWS_MCP_URL` 当普通 MCP
客户端连它。存在的意义是补上 Tushare 官方 MCP 的一个明确空洞——`news`/`cctv_news` 在实测里是
40203 无权限（见 CLAUDE.md 的权限矩阵），而快报的「产业链催化事件」段一直只能靠万得兜底或标未接入。

启动：
    python external_sources/china_news_server.py --port 8001
    # → http://127.0.0.1:8001/mcp

工具面移植自 claude-for-financial-services-cn/mcp-servers/china-news-mcp/server.py，
工具名与返回契约保持一致；改动同 akshare_server.py（默认 streamable-http + host/port 走 settings）。
"""

import argparse
import json
import sys
from datetime import datetime

import akshare as ak
import pandas as pd

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # 老版本 SDK 的入口
    from mcp import FastMCP

server = FastMCP(
    "china-news-mcp",
    instructions="免费财经新闻：个股新闻、市场头条（东方财富/akshare）",
)

# 单次返回上限：新闻条目文本长，不设限会把几百条塞进 LLM 上下文
_MAX_ROWS = 30


def _df_to_json(df: pd.DataFrame, limit: int = _MAX_ROWS) -> str:
    """DataFrame → records JSON 串；时间列转 ISO，其余转 str（新闻字段类型杂，统一成字符串最稳）。"""
    if df is None or df.empty:
        return json.dumps([], ensure_ascii=False)
    df = df.where(pd.notna(df), None)
    out = []
    for _, row in df.head(limit).iterrows():
        item = {}
        for col in df.columns:
            val = row[col]
            if isinstance(val, (datetime, pd.Timestamp)):
                val = val.isoformat()
            elif val is not None:
                val = str(val)
            item[str(col)] = val
        out.append(item)
    return json.dumps(out, ensure_ascii=False)


@server.tool()
def get_stock_news(ticker: str) -> str:
    """获取某只 A 股的最新相关新闻。

    Args:
        ticker: 6 位股票代码，如 "600519"。
    """
    try:
        return _df_to_json(ak.stock_news_em(symbol=ticker))
    except Exception as e:
        return json.dumps({"error": f"get_stock_news failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_market_headlines(top_n: int = 20) -> str:
    """获取 A 股市场最新头条/快讯。

    Args:
        top_n: 返回条数，默认 20，上限 50。
    """
    try:
        return _df_to_json(ak.stock_info_global_em(), limit=min(max(top_n, 1), 50))
    except Exception as e:
        return json.dumps({"error": f"get_market_headlines failed: {e}"}, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="China News MCP Server（免费财经新闻源）")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio", "sse"],
        default="streamable-http",
        help="默认 streamable-http（mcp_gateway 要的就是这个）",
    )
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    if args.transport == "stdio":
        print("Starting China News stdio MCP server…", file=sys.stderr)
        server.run(transport="stdio")
        return

    server.settings.host = args.host
    server.settings.port = args.port
    print(
        f"Starting China News {args.transport} server on http://{args.host}:{args.port}/mcp",
        file=sys.stderr,
    )
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
