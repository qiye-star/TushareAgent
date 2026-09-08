import type { SessionMessage, SessionMeta, TurnBlob } from './types'
import { parseQuickReport, type QuickReport } from './quickReport'
import type { McpSource, McpSourcesResponse, McpStatus } from './mcp'
import {
  parseSkillDetail,
  parseSkillList,
  type SkillDetail,
  type SkillDomain,
  type SkillRow,
} from './skills'

const jsonHeaders = { 'Content-Type': 'application/json' }

export async function listSessions(): Promise<SessionMeta[]> {
  const res = await fetch('/api/sessions')
  if (!res.ok) throw new Error(`无法获取会话列表 (${res.status})`)
  return res.json()
}

export async function getSessionTurns(sessionId: string): Promise<TurnBlob[]> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns`)
  if (!res.ok) throw new Error(`无法获取会话数据 (${res.status})`)
  return res.json()
}

export async function getSession(
  sessionId: string,
): Promise<SessionMessage[]> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`)
  if (res.status === 404) return []
  if (!res.ok) throw new Error(`无法获取会话 (${res.status})`)
  return res.json()
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`删除会话失败 (${res.status})`)
}

/** 读最近一份快报；尚未生成（404）→ null；形状不符 → null（绝不让解析错误崩页面）。 */
export async function getQuickReport(): Promise<QuickReport | null> {
  const res = await fetch('/api/quickreport/latest')
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`无法获取快报 (${res.status})`)
  return parseQuickReport(await res.json())
}

/** 手动触发生成（date 可选 YYYYMMDD 覆盖报告日）；409 进行中 → 抛错。 */
export async function generateQuickReport(date?: string | null): Promise<QuickReport | null> {
  const res = await fetch('/api/quickreport/generate', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ date: date ?? null }),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let detail = text
    try {
      detail = JSON.parse(text).detail ?? text
    } catch {
      /* keep raw */
    }
    throw new Error(typeof detail === 'string' ? detail : `快报生成失败 (${res.status})`)
  }
  return parseQuickReport(await res.json())
}

/** 历史快报摘要列表（日期降序）：{date, generated_at, missing, status_ok}。 */
export async function listQuickReportHistory(): Promise<QuickReportHistoryItem[]> {
  const res = await fetch('/api/quickreport/history')
  if (!res.ok) throw new Error(`无法获取快报历史 (${res.status})`)
  return res.json()
}

/** 按日读取存档快报（day = YYYY-MM-DD）；无存档 → null。 */
export async function getQuickReportByDate(day: string): Promise<QuickReport | null> {
  const res = await fetch(`/api/quickreport/report/${encodeURIComponent(day)}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`无法获取快报 (${res.status})`)
  return parseQuickReport(await res.json())
}

export interface QuickReportHistoryItem {
  date: string
  generated_at: string | null
  missing: string[]
  status_ok: boolean
}

export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
  } else {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
}

/** 读 MCP 全局运行时开关状态（启用/停用、是否已连接、工具数）。 */
export async function getMcpStatus(): Promise<McpStatus> {
  const res = await fetch('/api/settings/mcp')
  if (!res.ok) throw new Error(`无法获取 MCP 状态 (${res.status})`)
  return res.json()
}

/** 切换 MCP 全局开关（影响所有会话，立即生效，无需重启后端）。 */
export async function setMcpEnabled(enabled: boolean): Promise<McpStatus> {
  const res = await fetch('/api/settings/mcp', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`切换 MCP 开关失败 (${res.status})`)
  return res.json()
}

/** 按源状态（网关模式）：未配置 MCP_GATEWAY_URL 时 gateway_configured=false、sources=[]（不是错误）。 */
export async function getMcpSources(): Promise<McpSourcesResponse> {
  const res = await fetch('/api/settings/mcp/sources')
  if (!res.ok) throw new Error(`无法获取数据源列表 (${res.status})`)
  return res.json()
}

/** 切换单个数据源（仅网关模式下有效；未配置网关时后端返回 400）。 */
export async function setMcpSourceEnabled(id: string, enabled: boolean): Promise<McpSource> {
  const res = await fetch(`/api/settings/mcp/sources/${encodeURIComponent(id)}`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`切换数据源失败 (${res.status})`)
  return res.json()
}

export { jsonHeaders }

/** 报告技能库清单（技能页）。技能库被 SKILL_LIBRARY_ENABLED=false 关掉时返回空列表，不是错误。 */
export async function listSkills(): Promise<{ skills: SkillRow[]; domains: SkillDomain[] }> {
  const res = await fetch('/api/skills')
  if (!res.ok) throw new Error(`无法获取技能列表 (${res.status})`)
  return parseSkillList(await res.json())
}

/** 技能详情（正文 Markdown + 工具名对照 + 能力限制）；未知 id（404）→ null。 */
export async function getSkillDetail(id: string): Promise<SkillDetail | null> {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`无法获取技能详情 (${res.status})`)
  return parseSkillDetail(await res.json())
}

/** 启用/停用单个技能：停用后不再参与自动路由，也不能被「快速使用」强制指定。 */
export async function setSkillEnabled(id: string, enabled: boolean): Promise<boolean> {
  const res = await fetch(`/api/skills/${encodeURIComponent(id)}/toggle`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`切换技能开关失败 (${res.status})`)
  const body = (await res.json()) as { enabled?: unknown }
  return body.enabled !== false
}
