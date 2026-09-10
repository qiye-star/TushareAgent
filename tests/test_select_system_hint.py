"""select_system 的 report/compare 取数引导测试：确保提示词点名的清单与
`tool_select.REPORT_BASELINE_FAMILIES`（工具恒揭示层）保持同步，且「覆盖清单再收尾」的
额外提醒只在 report/compare 意图出现，不给 market 意图加无谓负担。
"""

from __future__ import annotations

from demomcp.graph.prompts import select_system
from demomcp.graph.tool_select import REPORT_BASELINE_FAMILIES


def test_select_system_report_hint_names_baseline_checklist() -> None:
    system = select_system("base", "report")
    for fam in REPORT_BASELINE_FAMILIES:
        assert fam in system, f"report 提示词应点名 {fam}（与 REPORT_BASELINE_FAMILIES 同步）"


def test_select_system_compare_hint_names_baseline_checklist() -> None:
    system = select_system("base", "compare")
    for fam in REPORT_BASELINE_FAMILIES:
        assert fam in system, f"compare 提示词应点名 {fam}（与 REPORT_BASELINE_FAMILIES 同步）"


def test_select_system_extra_coverage_clause_only_for_report_compare() -> None:
    marker = "还有没尝试过的科目"
    for intent in ("report", "compare"):
        system = select_system("base", intent, round_no=2, max_iterations=10)
        assert marker in system, f"{intent} 意图第 2 轮应带上「覆盖清单再收尾」提醒"
    market_system = select_system("base", "market", round_no=2, max_iterations=10)
    assert marker not in market_system  # market 意图不应被加这段
