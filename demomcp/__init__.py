"""demo-mcp：可扩展的 LLM+MCP 聊天机器人（分层框架）。

分层（依赖方向：entry → agents → interfaces；providers → interfaces；config 为叶子）：

    interfaces  契约层  类型 + 两协议（ToolProvider / LLMClient），纯契约无实现
    agents      核心层  agent 循环 + 工具注册（只依赖 interfaces）
    providers   实现层  可插拔适配器（tools: mcp/fake；llm: deepseek/mock）
    config      配置层  Settings + 路径检测 + stdio 参数
    entry       入口层  CLI（主）/ Web（可选）
"""
