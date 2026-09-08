"""网关自有的上游源实现（只被 mcp_gateway/sources.py 装配）。

放在这里而不是 demomcp/providers/tools/ 下，是为了让「新增数据源」不再往应用包里堆——
网关是唯一持有上游源的进程，源实现跟着它走。这些模块只依赖 demomcp 的**纯契约**
（interfaces.types 的 ToolSpec/ToolResult）与 MCP 客户端机器（providers.tools.mcp），
不反向依赖 agents/graph/entry 任何一层。

历史上的 wind.py 仍留在 demomcp/providers/tools/（有测试锁定其行为），网关照旧复用它；
新源一律落在本包内。
"""
