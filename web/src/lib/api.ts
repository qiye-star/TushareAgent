import type { SessionMessage, SessionMeta, TurnBlob } from './types'

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

export { jsonHeaders }
