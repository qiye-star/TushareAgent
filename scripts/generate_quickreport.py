"""独立生成快报（不需要 web 进程，但**需要 MCP 网关在跑**）：自己连一次网关取数。

用法：
    uv run scripts/generate_quickreport.py                 # 自动解析 T-1 交易日
    uv run scripts/generate_quickreport.py --date 20260904  # 指定报告日
    uv run scripts/generate_quickreport.py --no-save        # 只打印，不落盘

取数经 `MCP_GATEWAY_URL`（默认 http://127.0.0.1:8766/mcp）——上游源 token/key 只配在 mcp_gateway/.env。
与 web 同时运行时两者各连一条到网关的会话（不共享 web 的 app.state.tools_pool），
对快报这种低频生成可接受（CLAUDE.md 已注明脚本场景）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from demomcp.config.settings import Settings
from demomcp.providers.tools.mcp import mcp_tool_provider
from demomcp.quickreport.config import WatchlistConfig
from demomcp.quickreport.server import generate_from


async def main() -> int:
    parser = argparse.ArgumentParser(description="生成 AI 算力产业链高频跟踪快报")
    parser.add_argument("--date", help="报告日 YYYYMMDD（默认自动解析 T-1 交易日）")
    parser.add_argument("--no-save", action="store_true", help="不落盘 latest.json，只打印")
    parser.add_argument("--stage-timeout", type=float, default=60.0, help="单段取数上限秒（默认 60）")
    args = parser.parse_args()

    settings = Settings()
    cfg = WatchlistConfig.load()
    if cfg is None:
        print("[quickreport] watchlist 配置缺失或损坏（data/quickreport/watchlist.json）", file=sys.stderr)
        return 1

    try:
        async with mcp_tool_provider(
            settings.mcp_gateway_url, timeout=settings.mcp_timeout, retries=settings.mcp_retries
        ) as tools:
            report = await generate_from(
                tools, cfg, args.date,
                stage_timeout=args.stage_timeout,
                concurrency=settings.quickreport_concurrency,
                save=not args.no_save,
            )
    except Exception as exc:  # noqa: BLE001 - 命令行入口：任何失败（含非法 --date 格式）都转友好错误而非 traceback
        print(f"[quickreport] 生成失败：{exc}", file=sys.stderr)
        return 1

    print(f"快报已生成：{report['date']}（{report['sector']}）")
    for name in ("board", "watchlist", "announce", "forecast", "news"):
        sec = report.get(name, {})
        print(f"  - {name}: status={sec.get('status')}")
    if report.get("missing"):
        print(f"  missing: {', '.join(report['missing'])}")
    for e in report.get("errors", []):
        print(f"  ! {e['stage']}/{e['tool']}: {e['reason']}")
    print(f"  一句话研判（{report['brief']['chars']} 字）：{report['brief']['text']}")
    if args.no_save:
        print("\n---- JSON ----")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
