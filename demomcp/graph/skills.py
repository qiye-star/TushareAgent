"""投研报告 skill 注册表：把「某类结构化报告」的提示词封装成可被 router 命中的 skill。

设计要点：
- 每个 Skill 携带：id（router JSON 填 id）、name、description（触发描述，供 router 匹配）、
  system_prompt（(base)->合成器系统提示词，LLM 路径用；render 非空时优先用 render）、
  render（(evidence, ctx)->str 确定性渲染器，非空则 synthesizer 不走 LLM）、
  tool_hint（取数引导，追加进 select_system）、should_rag / strategy（覆盖该 skill 用例下的 RAG 取舍）。
- SKILLS = BUILTIN_SKILLS（内置，如快报）+ claude-for 技能库（63 条 vendored SKILL.md，由 skill_loader 装配）；
  新内置报告类型只需往 BUILTIN_SKILLS 加一条 entry；router 依 catalog_line/description 匹配，命中 → synthesizer 用该 skill 提示词。
- 快报 skill（ai_supply_chain_tracker）为首个成员：六段式高频跟踪快报，只复述「可用数据」、缺失标『数据未接入』、绝不编造。
- 本模块只依赖 prompts.ROUTER_BASE + tracker_render + skill_loader + config（不 import graph 内核，避免循环）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from demomcp.config.skill_toggle import is_skill_enabled
from demomcp.graph.prompts import ROUTER_BASE
from demomcp.graph.skill_loader import LoadedSkill, load_library
from demomcp.graph.tracker_render import render_tracker

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    """一份可被 router 命中的投研报告 skill 的定义。"""

    id: str
    name: str
    description: str                              # 触发描述：router 据此判断用户是否要生成这类报告
    system_prompt: Callable[[str], str]           # (base_system) -> synthesizer 系统提示词（LLM 路径用）
    render: Callable[[list[dict[str, Any]], dict[str, Any]], str] | None = None  # 确定性渲染器（非空则 synthesizer 不走 LLM）
    tool_hint: str = ""                           # 取数引导，追加进 select_system（找哪些接口/字段）
    tool_families: tuple[str, ...] = ()           # 接口名子串：tool_rag 的 select_tools 恒保留（不进 max_revealed 限额）
    should_rag: bool = False                      # 是否走年报 RAG（快报=行情/资金流/公告/新闻，默认跳过）
    strategy: str = "auto"                        # RAG 检索策略：factual/structural/auto
    report_type: str = ""                         # 输出类型标签（可选，供前端/归档）
    catalog_line: str | None = None               # router 清单用的短描述（None → 用 description；见决策 D6）


def build_router_system(skills: list[Skill] | None = None) -> str:
    """Router 系统提示词：基础意图分类 + （有 skill 时）可调用的报告 skill 清单。

    skills 为空/None → 纯基础提示词（向后兼容，已有测试不放 skills 也跑）。
    清单行优先用 `catalog_line`（导入技能的中文短描述，整张清单 ~3.5KB；用原始 description 会到 20.7KB），
    并**现读按技能开关**过滤（停用的技能不参与自然语言路由 → 改开关无需重启）。
    """
    base = ROUTER_BASE
    if not skills:
        return base
    enabled = [s for s in skills if is_skill_enabled(s.id)]
    if not enabled:
        return base
    catalog = "\n".join(f"- {s.id}：{s.name} —— {s.catalog_line or s.description}" for s in enabled)
    return (
        f"{base}\n\n"
        "【可调用的投研报告 skill】当用户要「生成某类结构化报告」而非单点查询/对比时，在上方 JSON 的 "
        "skill 字段填对应 id（intent 填其归类）；否则 skill 填 null。\n"
        f"{catalog}"
    )


def get_skill(skill_id: str | None) -> Skill | None:
    """按 id 取 skill；未知或空返回 None。"""
    if not skill_id:
        return None
    for s in SKILLS:
        if s.id == skill_id:
            return s
    return None


def _build_tracker_prompt(base: str) -> str:
    """AI算力产业链高频跟踪快报 —— synthesizer 系统提示词。

    六段式固定结构 + 硬「不编造」约束：只复述『可用数据』里的事实，某类数据取不到标『数据未接入』，
    绝不编数字/来源/结论；板块与标的池由用户问题动态确定，未给则标『未接入』。
    模板出处：docs/AI算力产业链高频跟踪快报_模板.md。
    """
    return (
        f"{base}\n\n"
        "请生成一份【产业链/板块高频跟踪快报】。板块与标的池以用户问题为准：用户说哪个产业链/板块就跟踪哪个，"
        "标的池用板块成分接口补齐；未给出则明确标注『未接入』。"
        "只依据下方『可用数据』中与问题相关的事实作答，**复述已结构化的数据**，绝不编造数字、来源或结论。\n\n"
        "【输出结构（严格按以下六段，用 `#### 一、…` 到 `#### 六、…` 小标题；标题以『【<用户所述产业链/板块名>跟踪快报·<当天日期>】』开头）】\n"
        "#### 一、板块概览\n"
        "- 用一个 Markdown 表格：概念/板块 | 当日涨跌幅 | 主力资金净流入(亿元) | 备注；再给『本周资金净流入 TOP5』表（排名/标的/金额）。"
        "（无可用的板块/资金流数据 → 该行或该表写『数据未接入』。）\n"
        "#### 二、标的池行情速览\n"
        "- 表格：标的 | 当日涨跌 | 最新价 | 本周涨幅 | 备注；标的池逐行，缺失字段写『—』，无成分数据 → 写『标的池未接入』。\n"
        "#### 三、关键公告\n"
        "- 表格：标的 | 公告类型 | 核心内容 | 发布日期（仅取业绩预告/股东增减持/重大合同等关键公告；无 → 写『本期无关键公告』）。\n"
        "#### 四、业绩预告异动提示\n"
        "- 仅当某标的预告净利润同比增速 >50% 或 <-20% 才列入，并给出触发与归因；无异常 → 写『本期无异常』。\n"
        "#### 五、产业链催化事件\n"
        "- 要点列表：从可用新闻/事件数据中挑与本产业链相关的催化；无 → 写『本期无明显催化』。\n"
        "#### 六、一句话研判\n"
        "- 一段 **≤150字** 的简评，只能基于上面各段已列出的数据，不得引入数据外信息。\n\n"
        "【格式与边界】\n"
        "- 全文数据驱动，叙述性文字占比控制在 20% 以内；结构数据用 Markdown 表格承载，数字/日期/百分比用 **加粗**。\n"
        "- 正文不写来源名/接口名/编号（来源由界面统一展示）；不加来源编号。\n"
        "- 某一类数据没取到：明确写『数据未接入』/『数据未更新』，不要空白、不要填占位假值、不要编造。\n"
        "- 不要输出与数据无关的套话；若几乎所有数据都未取到，如实说明后按结构占位。\n"
        "- 结尾加免责声明（沿用系统自动追加）。"
    )


TRACKER_TOOL_HINT = (
    "用户要产业链/板块跟踪快报。先用 list_apis 按『板块/概念/资金流/业绩预告/公告/新闻』等关键词浏览可用接口，"
    "再用 get_api_info 确认参数，最后用 query 或对应接口工具取板块行情、标的行情(daily/daily_basic)、"
    "业绩预告(forecast)、公告(announcement)、新闻 等数据；标的池取用户提到的板块/概念成分，拿不准先 get_api_info 再取数。"
)


tracker_skill = Skill(
    id="ai_supply_chain_tracker",
    name="AI算力产业链高频跟踪快报",
    description=(
        "用户要求生成某产业链/板块的高频跟踪快报——含板块涨跌、资金流、标的池行情、关键公告、"
        "业绩预告异动、产业链催化、一句话研判等多段结构化报告（如『生成AI算力产业链今日跟踪快报』『光模块板块每日观察』）。"
    ),
    render=render_tracker,      # 纯模板渲染：synthesizer 不走 LLM，直接由代码把 evidence 填进六段
    system_prompt=_build_tracker_prompt,  # 保留作为未 render 时的说明/回退（当前由 render 优先）
    tool_hint=TRACKER_TOOL_HINT,
    tool_families=("daily", "daily_basic", "forecast", "announcement", "moneyflow", "top_list", "concept", "ths_index"),
    should_rag=False,
    strategy="auto",
    report_type="daily_tracker",
)


BUILTIN_SKILLS: list[Skill] = [tracker_skill]


def _library_config() -> tuple[bool, str]:
    """读技能库开关与目录（走 Settings 以便 .env 生效）；读不到就按「开、包内默认目录」。"""
    try:
        from demomcp.config.settings import Settings

        cfg = Settings()
        return bool(cfg.skill_library_enabled), str(cfg.skill_library_dir or "")
    except Exception as exc:  # noqa: BLE001 - 配置读取失败不该让 import 崩
        _log.warning("skill_library 配置读取失败，按默认（启用+包内目录）：%s", exc)
        return True, ""


def to_skill(loaded: LoadedSkill) -> Skill:
    """`LoadedSkill`（claude-for 语料装配结果）→ router/tool_rag/synthesizer 认识的 `Skill`。

    `render=None` → 走 LLM 路径（不像 tracker 那样确定性渲染）；`system_prompt`/`tool_hint`
    是 LoadedSkill 的绑定方法（已包好本项目纪律段 + 工具名对照 + 能力限制，见 skill_loader）。
    """
    return Skill(
        id=loaded.id,
        name=loaded.name,
        description=loaded.description,
        system_prompt=loaded.system_prompt,
        render=None,
        tool_hint=loaded.tool_hint(),
        tool_families=loaded.tool_families,
        should_rag=loaded.should_rag,
        strategy=loaded.strategy,
        report_type=loaded.report_type,
        catalog_line=loaded.catalog_line,
    )


_LIBRARY_ENABLED, _LIBRARY_DIR = _library_config()


def load_skills(library_dir: str | None = None) -> list[Skill]:
    """装载 claude-for 技能库（`SKILL_LIBRARY_ENABLED=false` → 空列表）。"""
    if not _LIBRARY_ENABLED:
        return []
    return [to_skill(ls) for ls in load_library(library_dir or _LIBRARY_DIR or None)]


# 导入技能的原始装配结果（web API 的技能页详情直接读它；`SKILLS` 只保留图需要的部分）
LIBRARY: list[LoadedSkill] = load_library(_LIBRARY_DIR or None) if _LIBRARY_ENABLED else []

# 可扩展注册表：内置 skill + claude-for 技能库；新报告技能在 BUILTIN_SKILLS 追加一条 entry 即可被 router 命中。
SKILLS: list[Skill] = BUILTIN_SKILLS + [to_skill(ls) for ls in LIBRARY]
