"""快报阈值谓词 + 快报专用字段别名（补 tracker_render 的已知缺口）。

- `forecast_hit`/`range_hit`：业绩预告异动阈值（> up 或 < down，严格边界），watchlist.json 可配置。
  tracker_render._section_forecast/_summary_data 内为等价硬编码（>50/<-20）、被 tests/test_tracker_render.py
  锁住 Markdown 形态 —— **不要同步修改 tracker_render**；本模块与它各自演进，共享谓词只在本模块内。
- 别名缺口：tracker_render._FOR_YOY 缺 forecast 的 `p_change_min/p_change_max`（区间同比）与
  express 的 `yoy_net_profit`（单值同比）——不补齐的话第四段恒「无异常」。
"""

from __future__ import annotations

from typing import Any

from demomcp.graph.tracker_render import _FOR_YOY, _get  # 复用容错取值（首命中语义）

# —— 快快报专用别名（predicate 自持，供 projection 的 forecast 段用）——
# 业绩预告同比区间（forecast）：下界/上界字段（净利润变动幅度 %）
_YOY_RANGE_MIN = ("p_change_min", "p_change_down", "net_profit_yoy_low", "预告净利同比下限")
_YOY_RANGE_MAX = ("p_change_max", "p_change_up", "net_profit_yoy_high", "预告净利同比上限")
# 业绩快报单值同比（express）：tracker_render._FOR_YOY 兜底 + yoy_net_profit
_YOY_SINGLE = ("yoy_net_profit", "net_profit_yoy", *_FOR_YOY)
# 预告区间/报告期标识
_SCOPE = ("period", "end_date", "ann_date", "report_date", "公告期", "报告期")
# 新闻元信息。**中文列名必须排在前面**：`tracker_render._get` 是「别名作为 key 的小写子串」
# 匹配、首命中即返回，所以更具体的名字要先试（`资讯标题` 之于 `标题`）。
# 实测各源真实列名（2026-09-08，逐个实调）：
#   china_news.get_market_headlines -> 标题 / 摘要 / 发布时间 / 链接
#   china_news.get_stock_news       -> 关键词 / 新闻标题 / 新闻内容 / 发布时间 / 文章来源 / 新闻链接
#   ifind_query(search_news)        -> 资讯标题 / 资讯内容 / 日期 / URL
# tracker_render._NEWS 只有 ("title","新闻标题","content",...)：`标题`/`资讯标题` 都匹配不上
# （别名要是 key 的子串，`新闻标题` 不是 `标题` 的子串），`日期` 也匹配不上 _NEWS_TIME
# → 标题与时间会被静默丢空、project_news 再把无标题的行过滤掉，整段变 empty。
# 这几张表在 predicate 自持（tracker_render 契约上零改动）。
_NEWS_TITLE = ("资讯标题", "新闻标题", "标题", "title", "content", "summary", "text", "新闻内容")
_NEWS_SRC = ("文章来源", "来源", "媒体", "src", "source")
_NEWS_TIME = ("发布时间", "新闻时间", "日期", "datetime", "publish_time", "pub_time", "date")
_NEWS_URL = ("新闻链接", "链接", "url", "link")


def forecast_hit(value: float | None, *, up: float = 50.0, down: float = -20.0) -> bool:
    """单值是否触发异动（严格边界：value > up 或 value < down；恰等 50/-20 不触发）。"""
    if value is None:
        return False
    return value > up or value < down


def range_hit(
    p_min: float | None, p_max: float | None, *, up: float = 50.0, down: float = -20.0
) -> tuple[bool, list[str]]:
    """区间（p_min ≤ p_max）是否触发以及触发侧：max > up → "up"；min < down → "down"。

    单值场景传 p_min=p_max=v（等价 forecast_hit）。hits 为空 = 不触发；顺序固定 ["up","down"]（可能两者都有）。
    """
    hits: list[str] = []
    if p_max is not None and p_max > up:
        hits.append("up")
    if p_min is not None and p_min < down:
        hits.append("down")
    return bool(hits), hits


def yoy_of(row: dict[str, Any]) -> tuple[float | None, float | None]:
    """从一行里取净利润同比（%）：区间（p_change_min/max）优先；单值（yoy_net_profit/兜底别名）退化为 min=max。

    实测教训（2026-09-05 真实数据）：express/forecast 行里与「同比」同名的字段可能是**绝对金额（元）**
    （如 2968778000），直接当百分比会触发 29 亿% 的荒谬异动 → 单值与区间都做 ±500% 合理性守卫，
    超界视为脏数据（宁可漏标、绝不误导——「数据未接入」哲学）。
    """
    raw_min = _num_or_none(_get(row, *_YOY_RANGE_MIN))
    raw_max = _num_or_none(_get(row, *_YOY_RANGE_MAX))
    if raw_min is not None or raw_max is not None:
        p_min = _guard_pct(raw_min)
        p_max = _guard_pct(raw_max)
        # 脏值判定：字段里**出现了数值**却被 ±500 守卫吞掉 → 该侧是单位错乱（元/索引号）⇒ 整条弃。
        # 2026-09-07 审查：原先只吞脏侧，(-600, +60) 会被当 `(None, 60)` 报成「上涨异动」——
        # 首亏公司显示成上涨，方向完全相反（宁漏标、绝不误导）。
        if (raw_min is not None and p_min is None) or (raw_max is not None and p_max is None):
            return None, None
        return _sorted_bounds(p_min, p_max)
    single = _guard_pct(_num_or_none(_get(row, *_YOY_SINGLE)))
    if single is not None:
        return single, single
    return None, None


def _guard_pct(v: float | None) -> float | None:
    """同比百分比合理性守卫：|v| > 500（%）视为其它单位（金额/索引号），弃。"""
    if v is None:
        return None
    return v if abs(v) <= 500 else None


def scope_of(row: dict[str, Any]) -> str:
    """预告期标识（如 20260930 / 2026Q3），取不到 → ""。"""
    return str(_get(row, *_SCOPE) or "")


def news_title_of(row: dict[str, Any]) -> str:
    """新闻标题；取不到 → ""（调用方据此丢弃该行——无标题的新闻条目没有展示价值）。"""
    return str(_get(row, *_NEWS_TITLE) or "")


def news_meta_of(row: dict[str, Any]) -> tuple[str, str]:
    """(src, datetime)；取不到 → ("", "")。"""
    return str(_get(row, *_NEWS_SRC) or ""), str(_get(row, *_NEWS_TIME) or "")


def news_url_of(row: dict[str, Any]) -> str:
    """新闻跳转链接；取不到 → ""（前端优雅降级为纯文本，不臆造链接）。"""
    return str(_get(row, *_NEWS_URL) or "")


def _sorted_bounds(p_min: float | None, p_max: float | None) -> tuple[float | None, float | None]:
    """保证 min ≤ max（数据源可能只给了单侧）。"""
    if p_min is not None and p_max is not None and p_min > p_max:
        p_min, p_max = p_max, p_min
    return p_min, p_max


def _num_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
