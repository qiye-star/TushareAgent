"""AkShare MCP Server —— 免费 A 股数据源（东方财富，经开源 akshare 库）。

**独立进程、独立依赖**：不属于 demomcp 也不属于 mcp_gateway，不在主仓库 pyproject.toml 里。
自己 `pip install -r external_sources/requirements.txt` 后单独启动，网关经 `AKSHARE_MCP_URL`
当普通 MCP 客户端连它——与连 Tushare 官方 MCP 是同一条代码路径，没有任何特殊分支。

启动（默认 streamable-http，网关要的就是这个）：
    python external_sources/akshare_server.py --port 8000
    # → http://127.0.0.1:8000/mcp

工具面移植自 claude-for-financial-services-cn/mcp-servers/akshare-mcp/server.py，
工具名与返回契约逐字保持一致（对标的就是它的数据面）；改动只有两处：
  1. 传输默认 streamable-http（原版只有 stdio|sse；SSE 在 MCP 规范里已弃用，且本项目客户端
     `demomcp/providers/tools/mcp.py` 只实现了 streamable-http）；
  2. host/port 经 `server.settings` 设置而非传给 `run()`——不同 mcp SDK 版本 run() 的签名不一致，
     走 settings 是跨版本稳的那条路。

注意 akshare 的调用是**同步阻塞**的（内部 requests + pandas 抓东方财富）。FastMCP 会把同步
工具函数放线程池里跑，所以这里保持同步定义即可，不要手动包 async——包成 async 反而会把阻塞
调用搬到事件循环上，堵死整个 server。
"""

import argparse
import json
import sys
from datetime import date

import akshare as ak
import pandas as pd

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # 老版本 SDK 的入口
    from mcp import FastMCP

server = FastMCP(
    "akshare-mcp",
    instructions="免费 A 股数据（东方财富/akshare）：行情、历史 K 线、财报、行业分类、指数、基金",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _df_to_json(df: pd.DataFrame) -> str:
    """DataFrame → records 形态 JSON 串（NaN 归一成 null，日期走 str）。"""
    if df is None or df.empty:
        return json.dumps([], ensure_ascii=False)
    df = df.where(pd.notna(df), None)
    return json.dumps(df.to_dict(orient="records"), ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@server.tool()
def search_stock(keyword: str) -> str:
    """按代码或名称搜索 A 股股票，返回代码/名称。

    Args:
        keyword: 股票代码或名称片段，如 "600519" 或 "茅台"。
    """
    try:
        df = ak.stock_info_a_code_name()
        mask = df["code"].astype(str).str.contains(keyword, case=False) | df["name"].str.contains(
            keyword, case=False
        )
        return _df_to_json(df[mask].head(20))
    except Exception as e:
        return json.dumps({"error": f"search_stock failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_quote(ticker: str) -> str:
    """获取 A 股实时行情快照（价格、涨跌幅、成交量、换手率、PE、PB、市值等）。

    Args:
        ticker: 6 位股票代码，如 "600519"（不带交易所后缀）。
    """
    try:
        df = ak.stock_zh_a_spot_em()
        df = df[df["代码"] == ticker]
        if df.empty:
            return json.dumps({"error": f"ticker {ticker} not found"}, ensure_ascii=False)
        return _df_to_json(df)
    except Exception as e:
        return json.dumps({"error": f"get_quote failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_historical_data(
    ticker: str, start_date: str = "", end_date: str = "", frequency: str = "daily"
) -> str:
    """获取 A 股历史 OHLCV 行情（前复权）。

    Args:
        ticker: 6 位股票代码，如 "600519"。
        start_date: 起始日 YYYYMMDD，默认一年前。
        end_date: 结束日 YYYYMMDD，默认今天。
        frequency: "daily"（默认）/"weekly"/"monthly"。
    """
    try:
        if not end_date:
            end_date = date.today().strftime("%Y%m%d")
        if not start_date:
            start_date = date.today().replace(year=date.today().year - 1).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=ticker, period=frequency, start_date=start_date, end_date=end_date, adjust="qfq"
        )
        return _df_to_json(df)
    except Exception as e:
        return json.dumps({"error": f"get_historical_data failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_financials(ticker: str, statement_type: str = "income", period: str = "annual") -> str:
    """获取 A 股财务报表数据。

    Args:
        ticker: 6 位股票代码，如 "600519"。
        statement_type: "income"（利润表）/"balance"（资产负债表）/"cashflow"（现金流量表）。
        period: "annual"（年报）/"quarterly"（季报）。
    """
    try:
        type_map = {"income": "利润表", "balance": "资产负债表", "cashflow": "现金流量表"}
        df = ak.stock_financial_abstract_ths(
            symbol=ticker, indicator=type_map.get(statement_type, "利润表")
        )
        if df is None or df.empty:
            return json.dumps({"error": "no financial data returned"}, ensure_ascii=False)
        if period == "annual":
            return _df_to_json(df[df.iloc[:, 0].astype(str).str.contains("12-31")].head(5))
        return _df_to_json(df.head(8))
    except Exception as e:
        return json.dumps({"error": f"get_financials failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_industry_stocks(industry: str = "") -> str:
    """列出某行业的成分股（东方财富行业分类）；industry 留空则返回全部行业名。

    Args:
        industry: 中文行业名，如 "半导体"、"银行"、"白酒"。
    """
    try:
        board_df = ak.stock_board_industry_name_em()
        if not industry:
            cols = ["板块名称", "板块代码", "涨跌幅", "上涨家数", "下跌家数"]
            return _df_to_json(board_df[cols].head(50))
        return _df_to_json(ak.stock_board_industry_cons_em(symbol=industry))
    except Exception as e:
        return json.dumps({"error": f"get_industry_stocks failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_index_data(index_code: str = "000001") -> str:
    """获取 A 股指数历史行情（最近 500 个交易日）。

    Args:
        index_code: 指数代码。常用 000001(上证)/399001(深证)/399006(创业板)/000688(科创50)/
                    000300(沪深300)/000016(上证50)。
    """
    try:
        mapping = {
            "000001": "sh000001",
            "399001": "sz399001",
            "399006": "sz399006",
            "000688": "sh000688",
            "000300": "sh000300",
            "000016": "sh000016",
        }
        symbol = mapping.get(index_code, f"sh{index_code}" if index_code.startswith("00") else index_code)
        return _df_to_json(ak.stock_zh_index_daily(symbol=symbol).tail(500))
    except Exception as e:
        return json.dumps({"error": f"get_index_data failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_stock_info(ticker: str) -> str:
    """获取 A 股公司基本资料（所属行业、总市值、上市日期等）。

    Args:
        ticker: 6 位股票代码，如 "600519"。
    """
    try:
        return _df_to_json(ak.stock_individual_info_em(symbol=ticker))
    except Exception as e:
        return json.dumps({"error": f"get_stock_info failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_market_overview() -> str:
    """获取 A 股市场概览：涨幅前 10、跌幅前 10、成交额前 10。"""
    try:
        df = ak.stock_zh_a_spot_em()
        if df.empty:
            return json.dumps({"error": "no market data"}, ensure_ascii=False)

        def _rows(frame):
            return [{k: str(v) for k, v in r.items()} for r in frame.to_dict(orient="records")]

        return json.dumps(
            {
                "top_gainers": _rows(df.nlargest(10, "涨跌幅")),
                "top_losers": _rows(df.nsmallest(10, "涨跌幅")),
                "most_active": _rows(df.nlargest(10, "成交额")),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"get_market_overview failed: {e}"}, ensure_ascii=False)


@server.tool()
def get_fund_data(fund_code: str) -> str:
    """获取场内基金/ETF 实时行情。

    Args:
        fund_code: 基金代码，如 "510300"。
    """
    try:
        df = ak.fund_etf_spot_em()
        result = df[df["代码"] == fund_code]
        if result.empty:
            return json.dumps({"error": f"fund {fund_code} not found"}, ensure_ascii=False)
        return _df_to_json(result)
    except Exception as e:
        return json.dumps({"error": f"get_fund_data failed: {e}"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="AkShare MCP Server（免费 A 股数据源）")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio", "sse"],
        default="streamable-http",
        help="默认 streamable-http（mcp_gateway 要的就是这个）",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    if args.transport == "stdio":
        print("Starting AkShare stdio MCP server…", file=sys.stderr)
        server.run(transport="stdio")
        return

    # host/port 走 settings：不同 mcp SDK 版本 run() 的签名不一致，settings 是跨版本稳的那条路
    server.settings.host = args.host
    server.settings.port = args.port
    print(
        f"Starting AkShare {args.transport} server on http://{args.host}:{args.port}/mcp",
        file=sys.stderr,
    )
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
