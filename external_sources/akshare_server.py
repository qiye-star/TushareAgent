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
import subprocess
import sys
from datetime import date
from urllib.parse import urlencode

import akshare as ak
import pandas as pd

# 东方财富行情 CDN（82.push2 / push2his / datacenter-web 等）2026-09 实测：非浏览器 User-Agent
# （含 python-requests 默认 UA 与无 UA）直接 RST 连接——akshare 的行情接口是裸 requests.get 不带 UA，
# 会整片失败，而搜索/新闻域（search-api-web / finance）不校验。这里全局给 requests 会话补一个浏览器 UA，
# 老版本 akshare 自己带 UA 的接口不受影响（requests 会合并同名 header）。
try:
    import requests as _requests

    _BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    _session_init = _requests.sessions.Session.__init__

    def _ua_session_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        _session_init(self, *args, **kwargs)
        self.headers["User-Agent"] = _BROWSER_UA

    _requests.sessions.Session.__init__ = _ua_session_init  # type: ignore[method-assign]

    # ---- 东财行情/行业/个股域的第二道墙：TLS 客户端栈指纹 ----
    # UA 修好后发现 push2 系列 / datacenter-web 仍按 TLS 栈过滤：本机 python requests/httpx/
    # curl_cffi（OpenSSL/内置 libcurl）一律 Connection closed，系统 curl.exe（Windows Schannel 栈、
    # HTTP/1.1）放行。akshare 库内部是 requests，无法换栈——所以对东财域名的请求透明改道
    # curl.exe 子进程（同一 URL/参数/UA，返回形状按 requests.Response 复刻，akshare 内部无感知），
    # 其它域名（同花顺/新浪）仍走原 requests。**契约不变，只换传输层**。
    _em_requests_get = _requests.get
    _em_requests_post = _requests.post
    _em_session_request = _requests.sessions.Session.request

    def _curl_fetch(method: str, url: str, params: dict | None = None, **kw: object) -> object:
        headers = dict(kw.pop("headers", None) or {})  # type: ignore[arg-type]
        headers.setdefault("User-Agent", _BROWSER_UA)
        timeout = kw.pop("timeout", 20) or 20
        if isinstance(timeout, tuple):
            timeout = max((t for t in timeout if isinstance(t, (int, float))), default=20)
        qs = urlencode(params) if params else ""
        url_q = f"{url}?{qs}" if qs else url
        cmd = ["curl.exe", "-sS", "-X", method, "--max-time", str(int(timeout))]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        if method == "POST":
            body = kw.pop("data", None) or kw.pop("json", None)
            if body is not None:
                cmd += ["--data-binary", body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)]
        cmd.append(url_q)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 10,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except Exception as e:
            raise _requests.exceptions.ConnectionError(f"curl.exe failed: {e}") from e
        if proc.returncode != 0 or not proc.stdout:
            raise _requests.exceptions.ConnectionError(
                f"curl exit {proc.returncode}: {proc.stderr.strip()[:200]}"
            )
        resp = _requests.Response()
        resp.status_code = 200
        resp.url = url_q
        resp.encoding = "utf-8"
        resp._content = proc.stdout.encode("utf-8")
        resp.headers = {"content-type": "application/json;charset=utf-8"}  # type: ignore[assignment]
        resp.request = _requests.Request(method, url_q).prepare()  # type: ignore[attr-defined]
        return resp

    def _smart_get(url: str, *args: object, **kw: object) -> object:  # type: ignore[no-untyped-def]
        return _curl_fetch("GET", url, *args, **kw) if "eastmoney.com" in url else _em_requests_get(url, *args, **kw)

    def _smart_post(url: str, *args: object, **kw: object) -> object:  # type: ignore[no-untyped-def]
        return _curl_fetch("POST", url, *args, **kw) if "eastmoney.com" in url else _em_requests_post(url, *args, **kw)

    def _smart_session_request(self: object, method: str, url: str, *args: object, **kw: object) -> object:  # type: ignore[no-untyped-def]
        if "eastmoney.com" in url:
            return _curl_fetch(method, url, *args, **kw)
        return _em_session_request(self, method, url, *args, **kw)

    _requests.get = _smart_get  # type: ignore[assignment]
    _requests.post = _smart_post  # type: ignore[assignment]
    _requests.sessions.Session.request = _smart_session_request  # type: ignore[method-assign]
except ImportError:  # akshare 环境里 requests 永远在，防御性兜底
    pass

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
        # 「报告期」列形态随 akshare 版本漂移：年值是纯年份（"1998"），也有 "1998-12-31" 的形态；
        # 只认 "12-31" 会整表过滤成空——两种都算年报。
        if period == "annual":
            annual = df[df.iloc[:, 0].astype(str).str.contains(r"12-31|^\d{4}$", regex=True)]
            return _df_to_json(annual.head(5))
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
