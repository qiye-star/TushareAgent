r"""独立生成快报（不需要 web 进程，但**需要 MCP 网关在跑**）：自己连一次网关取数。

用法：
    uv run scripts/generate_quickreport.py                 # 自动解析 T-1 交易日
    uv run scripts/generate_quickreport.py --date 20260904  # 指定报告日
    uv run scripts/generate_quickreport.py --no-save        # 只打印，不落盘
    uv run scripts/generate_quickreport.py --if-stale       # 已是最新就跳过（幂等，供计划任务用）

进程外定时（推荐）：`--if-stale` 让本脚本**幂等**，于是「每日生成」与「降级后重试」
可以用同一条每小时任务解决，不需要单独的重试循环。它只要求**网关在跑**，不要求 web 进程。
进程内调度（QUICKREPORT_AUTO）只在 web 活着时才会到点触发——开发机 08:30 没开着 uvicorn
就永远不触发，这是「快报不自动更新」的真实原因之一。

    # Windows 计划任务（每小时，08:30 起）
    schtasks /Create /F /TN "TushareAgent\QuickReport" /SC HOURLY /MO 1 /ST 08:30 /RL LIMITED ^
      /TR "\"E:\TushareAgent\.venv\Scripts\python.exe\" E:\TushareAgent\scripts\generate_quickreport.py --if-stale"

    # Linux/容器（宿主 cron 或 k8s CronJob）
    0 * * * * docker compose exec -T demo python scripts/generate_quickreport.py --if-stale

不需要在任务里 cd：`Settings.model_config.env_file` 是绝对的 `PROJECT_ROOT/.env`，
`quickreport_dir()` 也从 `__file__` 派生，工作目录无关。
`/SC HOURLY` 周末照跑，但日期守卫（T-1 交易日不变 + 报告已存在）让它免费跳过。
与进程内调度并存是安全的：两者经同一个 `_is_latest_complete` 守卫，且 web 端点侧还有
`quickreport_lock` 互斥；本脚本不持那把锁，但 `store._atomic_write` 本身是
last-writer-wins、不会写出半截 JSON。

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
from demomcp.providers.tools.mcp import gateway_business_error, mcp_tool_provider
from demomcp.quickreport.config import WatchlistConfig
from demomcp.quickreport.server import generate_from


async def main() -> int:
    parser = argparse.ArgumentParser(description="生成 AI 算力产业链高频跟踪快报")
    parser.add_argument("--date", help="报告日 YYYYMMDD（默认自动解析 T-1 交易日）")
    parser.add_argument("--no-save", action="store_true", help="不落盘 latest.json，只打印")
    parser.add_argument("--stage-timeout", type=float, default=60.0, help="单段取数上限秒（默认 60）")
    parser.add_argument(
        "--if-stale",
        action="store_true",
        help="已是最新（日期匹配且必需段齐全）就直接退出 0——**幂等**，"
        "因此可以每小时跑一次：日常生成与降级重试用同一条计划任务解决，不需要单独的重试循环",
    )
    args = parser.parse_args()

    settings = Settings()
    cfg = WatchlistConfig.load()
    if cfg is None:
        print("[quickreport] watchlist 配置缺失或损坏（data/quickreport/watchlist.json）", file=sys.stderr)
        return 1

    try:
        async with mcp_tool_provider(
            settings.mcp_gateway_url,
            timeout=settings.mcp_timeout,
            retries=settings.mcp_retries,
            business_error=gateway_business_error,  # 见 web.py 同处注释：混合信封需聚合判据
        ) as tools:
            if args.if_stale:
                # 与调度器共用同一个守卫，语义完全一致（避免两套「算不算最新」的判断漂移）
                import asyncio as _asyncio

                from demomcp.quickreport.pipeline import _Ctx, resolve_report_date
                from demomcp.quickreport.scheduler import _is_latest_complete

                ctx = _Ctx(_asyncio.Semaphore(settings.quickreport_concurrency), args.stage_timeout)
                expected = await resolve_report_date(tools, ctx, args.date)
                if _is_latest_complete(expected, cfg.required_sections):
                    print(f"[quickreport] 已是最新（{expected}），跳过生成")
                    return 0
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
