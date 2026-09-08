import type { RawSse } from './types'

/**
 * POST a chat message and consume the SSE stream via fetch + ReadableStream.
 * Backend emits named events: thinking/text/tool_call/tool_result/process/done/error.
 * Returns { sessionId } once the turn completes (the `done` event carries it).
 */
export async function streamChat(
  url: string,
  body: {
    message: string
    session_id?: string | null
    model?: string | null
    mode?: 'quick' | 'agent' | null
    /** 技能页「快速使用」指定的报告技能 id；后端对未知/停用 id 只忽略、不报错。 */
    skill?: string | null
  },
  onEvent: (evt: RawSse) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '')
    throw new Error(text || `请求失败 (${res.status})`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const handleFrame = (frame: string) => {
    let event = 'message'
    let data = ''
    for (const rawLine of frame.split('\n')) {
      const line = rawLine.trimEnd()
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data = line.slice(5).trim()
    }
    if (!data) return
    let parsed: any
    try {
      parsed = JSON.parse(data)
    } catch {
      return
    }
    onEvent({ event, data: parsed })
  }

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // Frames are separated by a blank line "\n\n".
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      if (frame.trim()) handleFrame(frame)
    }
  }
}
