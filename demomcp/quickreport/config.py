"""快报配置：路径常量 + watchlist.json 加载/校验 → WatchlistConfig（frozen dataclass）。

watchlist.json 是快报模块唯一的配置输入（标的池/板块候选族/阈值/调度时间/新闻源/展示上限），
缺失或损坏 → `load()` 返回 None（fail-fast，由调用方决定 422 / 记错跳过），绝不用默认值静默顶替核心配置。
镜像 curate.py load_catalog 的容错哲学：核心字段缺失不可容，可选段（thresholds/schedule…）缺失可容（用默认值）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from demomcp.config.env import PROJECT_ROOT


def cn_tz() -> timezone:
    """Asia/Shanghai 的 tzinfo：优先系统/包数据库；Windows 无 tzdata 时回退固定 UTC+8（该时区无夏令时，等价）。"""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8))


def quickreport_dir() -> Path:
    """快报数据目录：QUICKREPORT_DIR 环境变量 > Settings（.env 的 QUICKREPORT_DIR）> 项目默认。

    2026-09-07 审查：原先只读环境变量——settings.py 的 `quickreport_dir`（别名 QUICKREPORT_DIR）
    声明了却无人消费，用户在 .env 里配置被静默忽略、读写仍落仓库 data/。
    先读环境变量：测试与脚本用 monkeypatch.setenv 隔离，走这一层即可。
    """
    raw = os.environ.get("QUICKREPORT_DIR")
    if raw:
        return Path(raw)
    try:
        from demomcp.config.settings import Settings

        cfg = Settings()
    except Exception:  # noqa: BLE001 - Settings 不可用（缺必填 key/无 .env）→ 用默认目录
        cfg = None
    configured = getattr(cfg, "quickreport_dir", None) if cfg is not None else None
    if configured:
        return Path(configured)
    return PROJECT_ROOT / "data" / "quickreport"


def watchlist_path() -> Path:
    return quickreport_dir() / "watchlist.json"


def latest_path() -> Path:
    return quickreport_dir() / "latest.json"


def last_error_path() -> Path:
    return quickreport_dir() / "last_error.json"


@dataclass(frozen=True)
class StockCfg:
    name: str
    ts_code: str
    source: str = "csv"
    remark: str = ""


@dataclass(frozen=True)
class BoardCfg:
    th_concepts: tuple[str, ...] = ()  # 环1：同花顺概念名（ths_index/ths_daily）
    sw_indexes: tuple[str, ...] = ()   # 环2：申万指数名（index_classify/sw_daily）
    indexes: tuple[str, ...] = ()      # 环3：通用指数名（index_basic/index_daily）
    dc_flow: bool = True               # 是否 best-effort 尝试东财板块资金流（moneyflow_ind_dc 等）


@dataclass(frozen=True)
class Thresholds:
    up: float = 50.0   # 业绩预告同比增幅阈值（%），严格 > 触发
    down: float = -20.0  # 同比降幅阈值（%），严格 < 触发


@dataclass(frozen=True)
class Schedule:
    hour: int = 8
    minute: int = 30
    tz: str = "Asia/Shanghai"


@dataclass(frozen=True)
class WatchlistConfig:
    version: int = 1
    name: str = "AI算力产业链"
    watchlist: tuple[StockCfg, ...] = ()
    board: BoardCfg = field(default_factory=BoardCfg)
    thresholds: Thresholds = field(default_factory=Thresholds)
    schedule: Schedule = field(default_factory=Schedule)
    news_sources: tuple[str, ...] = ("wallstreetcn", "sina")
    display_limit: int = 100

    # —— 便捷访问（pipeline 用）——
    def codes(self) -> list[str]:
        return [s.ts_code for s in self.watchlist]

    def name_to_code(self) -> dict[str, str]:
        return {s.name: s.ts_code for s in self.watchlist}

    @classmethod
    def load(cls, path: Path | str | None = None) -> WatchlistConfig | None:
        """读 watchlist.json → WatchlistConfig；缺失/损坏/空的 watchlist → None。

        可选段缺失（thresholds/schedule/board/news_sources/display_limit）→ 用默认值；
        核心字段缺失（无 watchlist 或为空列表）→ None（fail-fast）。
        """
        p = Path(path) if path is not None else watchlist_path()
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            watchlist = data.get("watchlist")
            if not isinstance(watchlist, list) or not watchlist:
                return None
            cfg_watchlist = tuple(
                StockCfg(
                    name=str(s.get("name", "")),
                    ts_code=str(s.get("ts_code", "")),
                    source=str(s.get("source", "csv")),
                    remark=str(s.get("remark", "")),
                )
                for s in watchlist
                if isinstance(s, dict) and s.get("ts_code")
            )
            if not cfg_watchlist:
                return None
            board_raw = data.get("board") if isinstance(data.get("board"), dict) else {}
            thr_raw = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}
            sch_raw = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
            return cls(
                version=int(data.get("version", 1)),
                name=str(data.get("name", "AI算力产业链")),
                watchlist=cfg_watchlist,
                board=BoardCfg(
                    th_concepts=tuple(str(x) for x in board_raw.get("th_concepts", []) if x),
                    sw_indexes=tuple(str(x) for x in board_raw.get("sw_indexes", []) if x),
                    indexes=tuple(str(x) for x in board_raw.get("indexes", []) if x),
                    dc_flow=bool(board_raw.get("dc_flow", True)),
                ),
                thresholds=Thresholds(
                    up=float(thr_raw.get("up", 50.0)),
                    down=float(thr_raw.get("down", -20.0)),
                ),
                schedule=Schedule(
                    hour=int(sch_raw.get("hour", 8)),
                    minute=int(sch_raw.get("minute", 30)),
                    tz=str(sch_raw.get("tz", "Asia/Shanghai")),
                ),
                news_sources=tuple(str(x) for x in data.get("news_sources", []) if x) or ("wallstreetcn", "sina"),
                display_limit=int(data.get("display_limit", 100)),
            )
        except (TypeError, ValueError) as exc:  # 字段类型不对（如 thresholds.up 是字符串）
            print(f"[quickreport] watchlist 配置解析失败：{exc}")
            return None


class ConfigError(Exception):
    """watchlist 配置缺失/损坏时抛出（web 端点转 422，scheduler 记错跳过）。"""
