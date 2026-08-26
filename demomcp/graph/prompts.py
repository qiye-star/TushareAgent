"""四节点状态机的系统提示词（工具无关，具体工具由 ToolProvider::list_tools 提供）。

Router 用严格 JSON 提示做意图分类；Tool/RAG 与 Synthesizer 在「domain 基础提示」（settings.system_prompt）
之上叠加各自的任务引导，从而让编排器尊重上层语义工具层/标的约束，不改即插即用。
"""

from __future__ import annotations

ROUTER_SYSTEM = (
    "你是金融数据助手的意图识别器。判断用户要哪种投研任务，仅返回一个 JSON 对象："
    '{"intent": "market" | "report" | "compare", "out_of_scope": false}。'
    "intent 含义：market=纯查行情/指标；report=财报/财务细节检索；compare=综合对比分析。"
    "若用户问的标的超出可查范围、或要查的数据/维度不存在、或明显与金融数据无关，置 out_of_scope=true。"
    "不要输出任何多余文本，只输出 JSON。"
)


def select_system(base: str, intent: str | None) -> str:
    """Tool/RAG 节点的取数引导：基于 domain 提示 + 意图给方向（不点名工具，由模型对照 tool 清单自选）。"""
    hint = {
        "market": "用户要行情/指标。从可用工具中选行情/指标类工具，给足标的与日期（区间）参数，注意复权口径。",
        "report": "用户要财报/财务细节。从可用工具中选财报/财务类工具，给足标的与报告期/期数。",
        "compare": "用户要对比分析。可能需要调用一次或多次取数工具，取回可比数据（多标的/多区间），便于对比。",
    }.get(intent or "", "用户要行情/指标。从可用工具中选行情/指标类工具，给足标的与日期参数。")
    return (
        f"{base}\n\n本轮意图：{intent or '未定'}。{hint}\n"
        "只给出必要的一次或多次工具调用，不要重复取相同的数；拿不准可用工具时先确认有哪些可用工具。"
    )


def synth_system(base: str) -> str:
    """Synthesizer 节点的总结引导：基于 domain 提示 + 取数证据，要求可溯源、不编造。"""
    return (
        f"{base}\n\n基于上述工具取数结果，用简洁中文总结回答用户问题；"
        "说明数据来源（列出用到的工具名），必要时给出关键数字；若数据不足以回答，如实说明。"
        "不要编造，不要假设工具结果里没有的数据。"
    )
