"""网关按源开关持久化：<data_dir>/sources.json 读写（原子写 + 未知 id 返回空字典）。"""

from __future__ import annotations

from mcp_gateway.toggle_store import load_toggle_state, save_source_enabled


def test_load_missing_returns_empty(tmp_path) -> None:
    assert load_toggle_state(tmp_path) == {}


def test_load_corrupt_returns_empty(tmp_path) -> None:
    (tmp_path / "sources.json").write_text("{not json", encoding="utf-8")
    assert load_toggle_state(tmp_path) == {}


def test_save_then_load_roundtrip(tmp_path) -> None:
    save_source_enabled(tmp_path, "tushare", True)
    save_source_enabled(tmp_path, "wind", False)
    assert load_toggle_state(tmp_path) == {"tushare": True, "wind": False}


def test_save_updates_single_id_without_clobbering_others(tmp_path) -> None:
    save_source_enabled(tmp_path, "tushare", True)
    save_source_enabled(tmp_path, "wind", True)
    save_source_enabled(tmp_path, "wind", False)
    assert load_toggle_state(tmp_path) == {"tushare": True, "wind": False}


def test_save_creates_parent_dir(tmp_path) -> None:
    nested = tmp_path / "nested" / "dir"
    save_source_enabled(nested, "tushare", True)
    assert (nested / "sources.json").exists()
