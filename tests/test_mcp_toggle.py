"""MCP 全局运行时开关持久化：data/settings/mcp_toggle.json 读写（原子写 + 默认回退 True）。"""

from __future__ import annotations

import json

from demomcp.config.mcp_toggle import load_mcp_enabled, save_mcp_enabled


def test_load_missing_defaults_true(tmp_path) -> None:
    assert load_mcp_enabled(tmp_path / "missing.json") is True


def test_load_corrupt_defaults_true(tmp_path) -> None:
    p = tmp_path / "mcp_toggle.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_mcp_enabled(p) is True


def test_load_non_dict_defaults_true(tmp_path) -> None:
    p = tmp_path / "mcp_toggle.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_mcp_enabled(p) is True


def test_save_then_load_roundtrip(tmp_path) -> None:
    p = tmp_path / "mcp_toggle.json"
    save_mcp_enabled(False, p)
    assert load_mcp_enabled(p) is False
    save_mcp_enabled(True, p)
    assert load_mcp_enabled(p) is True


def test_save_creates_parent_dir(tmp_path) -> None:
    p = tmp_path / "nested" / "dir" / "mcp_toggle.json"
    save_mcp_enabled(False, p)
    assert p.exists()
    body = json.loads(p.read_text(encoding="utf-8"))
    assert body["enabled"] is False
    assert "updated_at" in body


def test_save_does_not_leave_tmp_file(tmp_path) -> None:
    p = tmp_path / "mcp_toggle.json"
    save_mcp_enabled(True, p)
    leftovers = list(tmp_path.glob(".*.tmp"))
    assert leftovers == []
