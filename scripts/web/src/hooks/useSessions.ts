import { useCallback, useEffect, useState } from 'react'
import { deleteSession, getSession, listSessions } from '@/lib/api'
import {
  getSessionTitles,
  setSessionTitle,
  type SessionTitles,
} from '@/lib/storage'
import { deriveTitle } from '@/lib/format'
import type { SessionMeta } from '@/lib/types'

type DisplayTitle = Record<string, string>

export function useSessions() {
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [titles, setTitles] = useState<DisplayTitle>({})
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const metas = await listSessions()
      const overrides = getSessionTitles()
      const enriched = await Promise.all(
        metas.slice(0, 40).map(async (m) => {
          if (overrides[m.session_id]) return null // keep override
          try {
            const msgs = await getSession(m.session_id)
            const first = msgs.find((x) => x.role === 'user')?.content ?? ''
            return { id: m.session_id, label: deriveTitle(first) }
          } catch {
            return null
          }
        }),
      )
      const next: DisplayTitle = {}
      metas.forEach((m, i) => {
        next[m.session_id] =
          overrides[m.session_id] ??
          enriched[i]?.label ??
          deriveTitle(null)
      })
      setSessions(metas)
      setTitles(next)
    } catch {
      setSessions([])
      setTitles({})
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const remove = useCallback(
    async (id: string) => {
      await deleteSession(id)
      setSessionTitle(id, '') // clear any local override
      await refresh()
    },
    [refresh],
  )

  const rename = useCallback((id: string, title: string) => {
    const overrides = setSessionTitle(id, title) as SessionTitles
    setTitles((t) => ({ ...t, [id]: title.trim() ? title : deriveTitle(null) }))
    return overrides
  }, [])

  return { sessions, titles, loading, refresh, remove, rename }
}
