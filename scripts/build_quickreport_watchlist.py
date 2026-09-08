"""一次性 bootstrap：合并 data-clean CSV + 模板示例池 → data/quickreport/watchlist.json。

- CSV：`data-clean/人工智能数据公告企业.csv`（209 行，含 BOM，`mapping_name,code,market,official_name`）→ ts_code = f"{code}.{market}"。
- 模板池：docs/AI算力产业链高频跟踪快报_模板.md 里的示例标的（硬编码，含持仓备注）。
- 合并规则：按 ts_code 去重；模板条目优先（source="template"，其后 source="csv"）。
- 无标准代码的模板条目（如「C超纯」）→ warn 并跳过，绝不猜码（交给人工补）。
- 重跑幂等（覆盖输出文件）；输出 indent=2 / ensure_ascii=False / 提交进 git。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data-clean" / "人工智能数据公告企业.csv"
OUT_PATH = ROOT / "data" / "quickreport" / "watchlist.json"

# 模板示例池（docs/AI算力产业链高频跟踪快报_模板.md）：仅保留有标准代码的
_TEMPLATE_POOL: list[dict[str, str]] = [
    {"name": "新易盛", "ts_code": "300502.SZ", "remark": "800G/1.6T 光模块主力"},
    {"name": "天孚通信", "ts_code": "300394.SZ", "remark": "光器件"},
    {"name": "中际旭创", "ts_code": "300308.SZ", "remark": "光模块龙头"},
    {"name": "网宿科技", "ts_code": "300017.SZ", "remark": "CDN/IDC"},
    {"name": "亨通光电", "ts_code": "600487.SH", "remark": "光纤光缆/海缆"},
    {"name": "剑桥科技", "ts_code": "603083.SH", "remark": "光模块"},
    {"name": "旭光电子", "ts_code": "600353.SH", "remark": "电子陶瓷"},
]

# 模板里无标准代码、无法自动映射的条目（人工补齐后手动加进 watchlist.json）
_UNKNOWN_CODES = ["C超纯"]


def main() -> int:
    entries: dict[str, dict[str, str]] = {}

    # 1. 模板池优先
    for e in _TEMPLATE_POOL:
        entries[e["ts_code"]] = {"name": e["name"], "ts_code": e["ts_code"], "source": "template", "remark": e.get("remark", "")}
    for name in _UNKNOWN_CODES:
        print(f"[warn] 模板条目「{name}」无标准代码，跳过（请人工补进 watchlist.json）", file=sys.stderr)

    # 2. CSV 补充
    if not CSV_PATH.exists():
        print(f"[error] 缺少 CSV：{CSV_PATH}", file=sys.stderr)
        return 1
    n = 0
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            market = (row.get("market") or "").strip().upper()
            name = (row.get("mapping_name") or "").strip()
            if not code or not market or not name:
                continue
            ts_code = f"{code}.{market}"
            if ts_code in entries:
                continue  # 模板优先，CSV 不覆盖
            entries[ts_code] = {"name": name, "ts_code": ts_code, "source": "csv", "remark": ""}
            n += 1

    watchlist = sorted(entries.values(), key=lambda e: e["ts_code"])
    config = {
        "version": 1,
        "name": "AI算力产业链",
        "watchlist": watchlist,
        "board": {
            # 环1 同花顺概念、环2 申万指数：多数 token 无权限（40203）→ 默认清空；
            # 环3 核心指数（上证/沪深300/中证500/创业板指/上证50/科创50，已实测可用）
            "th_concepts": [],
            "sw_indexes": [],
            "indexes": ["上证指数", "沪深300", "中证500", "创业板指", "上证50", "科创50"],
            "dc_flow": False,
        },
        "thresholds": {"up": 50.0, "down": -20.0},
        "schedule": {"hour": 8, "minute": 30, "tz": "Asia/Shanghai"},
        "news_sources": ["wallstreetcn", "sina"],
        "display_limit": 100,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"[ok] 已写入 {OUT_PATH}：模板 {len(_TEMPLATE_POOL)} 只 + CSV {n} 只 = 共 {len(watchlist)} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
