"""MCP 网关：独立部署的服务，聚合多个上游 MCP 源（Tushare 官方 MCP / 万得 Wind / 未来更多），
按源开关、动态把当前启用的源的工具聚合暴露成一个 streamable-http MCP 端点。

与 demomcp 同一个 git 仓库、复用其 providers.tools.{mcp,wind} 的连接管理代码，但独立进程/独立端口/
独立配置/独立部署生命周期，见 mcp_gateway/app.py 与仓库根 docker-compose.yml 的 mcp-gateway service。
"""
