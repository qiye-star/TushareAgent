"""quickreport 配置层：watchlist.json 加载/校验 + 阈值谓词（forecast_hit/range_hit/yoy_of）。"""

from __future__ import annotations

import json

from demomcp.quickreport.config import WatchlistConfig
from demomcp.quickreport.predicate import forecast_hit, range_hit, yoy_of


def _make(tmp_path, **overrides) -> str:
    data = {
        "version": 1,
        "name": "AI算力产业链",
        "watchlist": [{"name": "新易盛", "ts_code": "300502.SZ", "source": "template", "remark": "光模块"}],
        "board": {"th_concepts": ["光模块(CPO)"], "sw_indexes": [], "indexes": [], "dc_flow": True},
        "thresholds": {"up": 50.0, "down": -20.0},
        "schedule": {"hour": 8, "minute": 30, "tz": "Asia/Shanghai"},
        "news_sources": ["wallstreetcn"],
        "display_limit": 100,
    }
    data.update(overrides)
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_load_valid(tmp_path) -> None:
    cfg = WatchlistConfig.load(_make(tmp_path))
    assert cfg is not None
    assert cfg.name == "AI算力产业链"
    assert len(cfg.watchlist) == 1
    assert cfg.codes() == ["300502.SZ"]
    assert cfg.name_to_code() == {"新易盛": "300502.SZ"}
    assert cfg.thresholds.up == 50.0 and cfg.thresholds.down == -20.0
    assert cfg.schedule.hour == 8 and cfg.schedule.minute == 30
    assert cfg.board.th_concepts == ("光模块(CPO)",)
    assert cfg.news_sources == ("wallstreetcn",)


def test_load_missing_file_returns_none(tmp_path) -> None:
    assert WatchlistConfig.load(tmp_path / "nope.json") is None


def test_load_corrupt_json_returns_none(tmp_path) -> None:
    p = tmp_path / "watchlist.json"
    p.write_text("{not json", encoding="utf-8")
    assert WatchlistConfig.load(p) is None


def test_load_non_dict_returns_none(tmp_path) -> None:
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert WatchlistConfig.load(p) is None


def test_load_empty_watchlist_returns_none(tmp_path) -> None:
    assert WatchlistConfig.load(_make(tmp_path, watchlist=[])) is None
    # 全条目无 ts_code → 也是 None
    assert WatchlistConfig.load(_make(tmp_path, watchlist=[{"name": "x"}])) is None


def test_load_optional_sections_defaulted(tmp_path) -> None:
    data = {"watchlist": [{"name": "新易盛", "ts_code": "300502.SZ"}]}
    p = tmp_path / "watchlist.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cfg = WatchlistConfig.load(p)
    assert cfg is not None
    assert cfg.thresholds.up == 50.0 and cfg.thresholds.down == -20.0
    assert cfg.schedule.hour == 8 and cfg.schedule.minute == 30
    assert cfg.display_limit == 100


def test_load_bad_field_type_returns_none(tmp_path) -> None:
    assert WatchlistConfig.load(_make(tmp_path, thresholds={"up": "oops", "down": -20})) is None


# —— 阈值谓词 ——


def test_forecast_hit_boundaries() -> None:
    assert forecast_hit(50.0) is False  # 严格边界：恰等不触发
    assert forecast_hit(50.01) is True
    assert forecast_hit(-20.0) is False
    assert forecast_hit(-20.01) is True
    assert forecast_hit(0.0) is False
    assert forecast_hit(None) is False


def test_forecast_hit_custom_thresholds() -> None:
    assert forecast_hit(30.0, up=25.0, down=-10.0) is True
    assert forecast_hit(30.0, up=50.0, down=-20.0) is False


def test_range_hit_semantics() -> None:
    # max>up → 仅 up
    hit, hits = range_hit(10.0, 60.0)
    assert hit is True and hits == ["up"]
    # min<down → 仅 down
    hit, hits = range_hit(-25.0, 10.0)
    assert hit is True and hits == ["down"]
    # 双触发
    hit, hits = range_hit(-30.0, 80.0)
    assert hit is True and hits == ["up", "down"]
    # 区间内不触发
    assert range_hit(10.0, 40.0)[0] is False
    # 单侧 None
    assert range_hit(None, 60.0)[0] is True
    assert range_hit(-30.0, None)[0] is True
    assert range_hit(None, None)[0] is False


def test_yoy_of_alias_coverage() -> None:
    """守住「第四段恒无异常」bug：forecast 区间字段与 express 单值字段都能取值。"""
    assert yoy_of({"p_change_min": 60.0, "p_change_max": 110.0}) == (60.0, 110.0)
    assert yoy_of({"p_change_max": 110.0}) == (None, 110.0)
    assert yoy_of({"p_change_min": -25.0, "p_change_max": 5.0}) == (-25.0, 5.0)
    assert yoy_of({"yoy_net_profit": 102.93}) == (102.93, 102.93)
    assert yoy_of({"net_profit_yoy": 30.0}) == (30.0, 30.0)
    assert yoy_of({"未命名": "x"}) == (None, None)
    # 上下界颠倒时修正（数据源只给一侧或名称混淆）
    assert yoy_of({"p_change_min": 80.0, "p_change_max": 60.0}) == (60.0, 80.0)


def test_yoy_of_guards_absolute_amount_units() -> None:
    """±500% 合理性守卫：绝对金额（元）被误当百分比 → 弃（2968778000 不再触发 29 亿% 荒谬异动）。"""
    assert yoy_of({"yoy_net_profit": 2968778000.0}) == (None, None)
    assert yoy_of({"p_change_min": 2968778000.0, "p_change_max": 2968778000.0}) == (None, None)
    assert yoy_of({"yoy_net_profit": -6000.0}) == (None, None)
    assert yoy_of({"p_change_max": 550.0}) == (None, None)  # 单侧超界 → 整条弃
    assert yoy_of({"p_change_min": 100.0, "p_change_max": 480.0}) == (100.0, 480.0)  # 正常区间不受影响


def test_yoy_of_dirty_side_discards_whole_row() -> None:
    """2026-09-07 审查回归：一侧脏（超 ±500）另一侧正常时**整条弃**，而不是把脏侧置 None 后
    用正常侧单独判定——(-600, +60) 曾被报成「上涨异动」（首亏公司显示成正向，方向相反）。"""
    assert yoy_of({"p_change_min": -600.0, "p_change_max": 60.0}) == (None, None)
    assert yoy_of({"p_change_min": -600.0, "p_change_max": -500.0}) == (None, None)
    # 合法单侧（数据源只给了单边界）不受影响
    assert yoy_of({"p_change_max": 110.0}) == (None, 110.0)
    assert yoy_of({"p_change_min": -25.0}) == (-25.0, None)
