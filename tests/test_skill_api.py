"""技能库对外面：/api/skills 三端点 + 按技能开关 + 「快速使用」的 forced_skill 链路。

离线：不建工具池（`TOOL_POOL_HOT_START=false`）、不连 LLM（强制 skill 链路用 MockLLM 直接跑 Agent）。
开关落盘走 tmp_path，绝不碰仓库里的 data/settings/skills.json。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from demomcp.config.skill_toggle import (
    is_skill_enabled,
    load_skill_toggles,
    save_skill_enabled,
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TOOL_POOL_HOT_START", "false")   # TestClient 会跑真 lifespan：别去连 MCP
    monkeypatch.setenv("QUICKREPORT_AUTO", "false")
    from demomcp.entry.web import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 列表 / 详情 / 开关
# ---------------------------------------------------------------------------


def test_list_skills_returns_library_with_domains(client: TestClient) -> None:
    body = client.get("/api/skills").json()
    assert len(body["skills"]) == 63
    ids = [s["id"] for s in body["skills"]]
    assert "china-dcf" in ids and "china-gl-recon-fund" in ids
    row = next(s for s in body["skills"] if s["id"] == "china-dcf")
    assert row["name"] == "A股DCF估值"
    assert row["domain"] == "china-finance" and row["domain_label"] == "股票研究"
    assert row["enabled"] is True
    assert row["should_rag"] is True
    assert "body_markdown" not in row, "列表行不该带全文（63 × 6KB）"
    assert {d["id"] for d in body["domains"]} == {
        "china-finance", "investment-banking", "private-equity",
        "wealth-management", "fund-admin", "operations",
    }
    assert sum(d["count"] for d in body["domains"]) == 63


def test_skill_detail_has_body_and_tool_mapping(client: TestClient) -> None:
    body = client.get("/api/skills/china-earnings-analysis").json()
    assert body["body_markdown"].startswith("#")
    assert "Step 6: Draft the Report" in body["body_markdown"]
    olds = {m["old"] for m in body["tool_mapping"]}
    assert "get_financials" in olds
    assert body["capability_note"] is None          # 非文件产物型
    assert body["source_families"]


def test_files_limited_skill_exposes_capability_note(client: TestClient) -> None:
    body = client.get("/api/skills/china-xlsx-author").json()
    assert body["files_limited"] is True
    assert body["capability_note"] and "无文件读写" in body["capability_note"]


def test_unknown_skill_404(client: TestClient) -> None:
    assert client.get("/api/skills/nope").status_code == 404
    assert client.post("/api/skills/nope/toggle", json={"enabled": False}).status_code == 404


def test_toggle_persists_and_filters_router_catalog(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """停用的技能不进 router 清单（现读开关 → 无需重启）；未记录的 id 默认启用。"""
    from demomcp.config import skill_toggle
    from demomcp.graph.skills import SKILLS, build_router_system

    path = tmp_path / "skills.json"
    monkeypatch.setattr(skill_toggle, "skill_toggle_path", lambda: path)

    assert is_skill_enabled("china-dcf") is True         # 无记录 → 默认开
    assert "china-dcf：" in build_router_system(SKILLS)

    save_skill_enabled("china-dcf", False)
    assert load_skill_toggles() == {"china-dcf": False}
    assert is_skill_enabled("china-dcf") is False
    catalog = build_router_system(SKILLS)
    assert "china-dcf：" not in catalog
    assert "china-comps：" in catalog                     # 只影响被停用那一条

    save_skill_enabled("china-dcf", True)
    assert "china-dcf：" in build_router_system(SKILLS)


def test_toggle_survives_unwritable_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """落盘失败只记 WARNING，不让开关请求 500。"""
    from demomcp.config import skill_toggle

    monkeypatch.setattr(skill_toggle, "skill_toggle_path", lambda: tmp_path / "nodir" / "x" / "s.json")
    monkeypatch.setattr(skill_toggle.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert save_skill_enabled("china-dcf", False) == {"china-dcf": False}


# ---------------------------------------------------------------------------
# 「快速使用」：forced_skill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forced_skill_bypasses_router_catalog(make_settings) -> None:
    """指定 skill 时：router system **不带**技能清单（省 ~4KB），且即使 LLM 填了别的 skill 也以指定值为准。"""
    from demomcp.interfaces.types import ChatResponse
    from tests.test_agent_loop import SPECS, FakeToolProvider, MockLLM

    tools = FakeToolProvider(SPECS, {})
    mock = MockLLM([
        ChatResponse(stop_reason="end_turn", text='{"intent":"report","skill":"ai_supply_chain_tracker","out_of_scope":false}'),
    ])
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    from demomcp.agents.agent import Agent

    await Agent(llm=mock, tools=tools, config=make_settings()).run(
        "给比亚迪做一份DCF估值", forced_skill="china-dcf", on_process=on_process
    )

    intent_ev = next(d for k, d in events if k == "intent")
    assert intent_ev["skill"] == "china-dcf"            # forced 优先于 LLM 返回的 skill
    assert intent_ev["skill_name"] == "A股DCF估值"       # 前端「命中技能：xxx」直接用它
    router_system = mock.calls[0]["system"]
    assert "可调用的投研报告 skill" not in router_system, "forced 时不该再拼 63 行清单"


@pytest.mark.asyncio
async def test_forced_skill_overrides_quick_mode(make_settings) -> None:
    """quick 模式传 skills=[]，会让指定技能静默失效——后端保护：有 forced_skill 就按 agent 跑（决策 O1）。"""
    from demomcp.interfaces.types import ChatResponse
    from tests.test_agent_loop import SPECS, FakeToolProvider, MockLLM

    tools = FakeToolProvider(SPECS, {})
    mock = MockLLM([ChatResponse(stop_reason="end_turn", text='{"intent":"report","skill":null,"out_of_scope":false}')])
    events: list[tuple[str, dict]] = []

    async def on_process(kind: str, data: dict) -> None:
        events.append((kind, data))

    from demomcp.agents.agent import Agent

    result = await Agent(llm=mock, tools=tools, config=make_settings()).run(
        "给比亚迪做一份DCF估值", mode="quick", forced_skill="china-dcf", on_process=on_process
    )
    assert result.mode == "agent"
    assert next(d for k, d in events if k == "intent")["skill"] == "china-dcf"


def test_chat_request_drops_unknown_or_disabled_skill(client: TestClient) -> None:
    """未知/停用 id 只是被忽略（按普通路由跑），不该 422/500 —— 前端可能拿着过期 id。"""
    from demomcp.entry.web import ChatRequest

    assert ChatRequest(message="hi", skill="nope").skill == "nope"   # 校验发生在 handler 里
    assert ChatRequest(message="hi").skill is None
