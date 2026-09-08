"""快报（quickreport）：独立于 Agent 主链路的确定性取数+计算管线。

六段式「AI算力产业链高频跟踪快报」（板块概览/标的池行情速览/关键公告/业绩预告异动/
产业链催化事件/一句话研判），取数与计算完全不经过 LLM：
`tools.call_tool(...)` 原始取数 → （可配置）阈值过滤 → 结构化 JSON → 前端渲染。

设计约束（与 CLAUDE.md 的架构约定一致）：
- 本包只依赖 `interfaces`（ToolProvider 协议）+ `config` + `graph.tracker_render` 的**无状态纯函数**
  （仅 import，文件本身零改动）；取数用的 ToolProvider 由调用方注入——web 传工具池租约，
  `scripts/generate_quickreport.py` 自己连一次 MCP 网关（`MCP_GATEWAY_URL`）。
- 不注册进 graph/skills.py、不依赖 graph/nodes|builder|routes —— 与 Agent/LLM 主链路解耦。
- 缺失 =「数据未接入 / 本期无…」status 标志，绝不编造（沿用 tracker_render 哲学）。

本 __init__.py 故意无 import（零副作用，同 demomcp/rag/__init__ 约定）。
"""
