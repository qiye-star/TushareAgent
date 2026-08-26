"""语义工具层 · 纯函数校验离线测试：代码解析(硬 allowlist)、日期/复权/期次归一化。"""

from __future__ import annotations

from datetime import date

import pytest

from demomcp.providers.tools.stocks import (
    StockInputError,
    normalize_adj,
    normalize_date_range,
    normalize_period,
    parse_date,
    period_label,
    resolve_stock,
)

TODAY = date(2026, 8, 25)


def test_resolve_stock_aliases() -> None:
    assert resolve_stock("比亚迪") == "002594.SZ"
    assert resolve_stock("比亚迪股份") == "002594.SZ"
    assert resolve_stock("BYD") == "002594.SZ"
    assert resolve_stock("002594") == "002594.SZ"
    assert resolve_stock("002594.SZ") == "002594.SZ"
    assert resolve_stock("宁德时代") == "300750.SZ"
    assert resolve_stock("catl") == "300750.SZ"
    assert resolve_stock("300750") == "300750.SZ"
    assert resolve_stock(" 300750.sz ") == "300750.SZ"


def test_resolve_stock_allowlist_reject() -> None:
    with pytest.raises(StockInputError) as exc:
        resolve_stock("600519")
    assert "仅支持" in str(exc.value)
    assert "600519" in str(exc.value)


def test_resolve_stock_empty() -> None:
    with pytest.raises(StockInputError):
        resolve_stock("")
    with pytest.raises(StockInputError):
        resolve_stock("   ")


@pytest.mark.parametrize("token,expected", [
    ("20260801", "20260801"),
    ("2026-08-01", "20260801"),
    ("2026/08/01", "20260801"),
    ("2026.08.01", "20260801"),
    ("2026-8-1", "20260801"),
])
def test_parse_date_formats(token: str, expected: str) -> None:
    assert parse_date(token, today=TODAY) == expected


def test_parse_date_relative() -> None:
    assert parse_date("今天", today=TODAY) == "20260825"
    assert parse_date("today", today=TODAY) == "20260825"
    assert parse_date("近一年", today=TODAY) == "20250825"
    assert parse_date("近一月", today=TODAY) == "20260726"
    assert parse_date("今年", today=TODAY) == "20260101"
    assert parse_date("去年", today=TODAY) == "20251231"


def test_parse_date_invalid() -> None:
    with pytest.raises(StockInputError):
        parse_date("abc", today=TODAY)
    with pytest.raises(StockInputError):
        parse_date("20261301", today=TODAY)  # 非法月份/日期


def test_parse_date_date_object() -> None:
    assert parse_date(date(2024, 3, 31), today=TODAY) == "20240331"


def test_normalize_adj() -> None:
    assert normalize_adj(None) == "qfq"
    assert normalize_adj("qfq") == "qfq"
    assert normalize_adj("前复权") == "qfq"
    assert normalize_adj("hfq") == "hfq"
    assert normalize_adj("后复权") == "hfq"
    assert normalize_adj("不复权") == "none"
    assert normalize_adj("") == "qfq"
    with pytest.raises(StockInputError):
        normalize_adj("wtf")


def test_normalize_period_default_and_relative() -> None:
    assert len(normalize_period(None, today=TODAY)) == 8  # 默认近 8 期
    assert len(normalize_period("近一年", today=TODAY)) == 4
    assert all(p.endswith(("0331", "0630", "0930", "1231")) for p in normalize_period("近两年", today=TODAY))


def test_normalize_period_explicit() -> None:
    assert normalize_period("2024年报", today=TODAY) == ["20241231"]
    assert normalize_period("2024", today=TODAY) == ["20241231"]
    assert normalize_period("2024Q1", today=TODAY) == ["20240331"]
    assert normalize_period("20241Q", today=TODAY) == ["20240331"]
    assert normalize_period("20240630", today=TODAY) == ["20240630"]


def test_normalize_period_invalid() -> None:
    with pytest.raises(StockInputError):
        normalize_period("随便", today=TODAY)


def test_period_label() -> None:
    assert period_label("20240331") == "2024Q1"
    assert period_label("20241231") == "2024年报"
    assert period_label("20240630") == "2024Q2"


def test_normalize_date_range_default_and_order() -> None:
    start, end = normalize_date_range("2026-01-01", "2026-06-30", today=TODAY)
    assert (start, end) == ("20260101", "20260630")
    with pytest.raises(StockInputError):
        normalize_date_range("2026-06-30", "2026-01-01", today=TODAY)


def test_normalize_date_range_clamps_future_end() -> None:
    _, end = normalize_date_range("2026-01-01", "2030-01-01", today=TODAY)
    assert end == "20260825"  # 未来结束日期按今日截断
