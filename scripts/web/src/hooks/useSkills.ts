import { useCallback, useEffect, useState } from 'react'
import { listSkills, setSkillEnabled } from '@/lib/api'
import type { SkillDomain, SkillRow } from '@/lib/skills'

/**
 * 技能库列表 + 启用/停用（乐观更新，失败回滚）。
 *
 * 镜像 useMcpSources 的形态：加载失败只记 error 字符串给页面显示，不抛到渲染层。
 */
export function useSkills() {
  const [skills, setSkills] = useState<SkillRow[]>([])
  const [domains, setDomains] = useState<SkillDomain[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const body = await listSkills()
      setSkills(body.skills)
      setDomains(body.domains)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载技能库失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const toggle = useCallback(async (id: string, enabled: boolean) => {
    setSkills((rows) => rows.map((r) => (r.id === id ? { ...r, enabled } : r)))
    try {
      const actual = await setSkillEnabled(id, enabled)
      setSkills((rows) => rows.map((r) => (r.id === id ? { ...r, enabled: actual } : r)))
      setError(null)
    } catch (err) {
      setSkills((rows) => rows.map((r) => (r.id === id ? { ...r, enabled: !enabled } : r)))
      setError(err instanceof Error ? err.message : '切换技能开关失败')
    }
  }, [])

  return { skills, domains, loading, error, refresh, toggle }
}
