/** 对应后端 GET/POST /api/settings/mcp 的响应形状（entry/web.py::_mcp_status）。 */
export interface McpStatus {
  enabled: boolean
  connected: boolean
  tool_count: number | null
}

/** 网关按源状态（mcp_gateway/admin.py::list_sources，经 demomcp /api/settings/mcp/sources 转发）。 */
export interface McpSource {
  id: string
  display_name: string
  enabled: boolean
  connected: boolean
  tool_count: number | null
}

/**
 * GET /api/settings/mcp/sources 的整体响应。网关不可达/没配好时后端也返回 200，
 * 用 reachable / error / config_problems 把运维态原样带过来（前端要显示出来，不是隐藏）。
 */
export interface McpSourcesResponse {
  gateway_url: string
  reachable: boolean
  error: string | null
  config_problems: string[]
  sources: McpSource[]
}
