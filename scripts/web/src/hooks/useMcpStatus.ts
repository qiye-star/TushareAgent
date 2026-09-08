import { useCallback, useEffect, useRef, useState } from 'react'
import { getMcpStatus, setMcpEnabled } from '@/lib/api'
import type { McpStatus } from '@/lib/mcp'

/**
 * MCP 全局运行时开关状态（镜像 useQuickReport 的 useCallback + useState + 挂载 effect 模式）。
 * refresh：手动/挂载时拉取当前状态；toggle：切换开关，直接用后端返回值更新本地状态（不必再 GET 一次）。
 */
export function useMcpStatus() {
  const [status, setStatus] = useState<McpStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getMcpStatus()
      if (mounted.current) setStatus(data)
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '网络异常')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  const toggle = useCallback(async (enabled: boolean) => {
    setToggling(true)
    setError(null)
    try {
      const data = await setMcpEnabled(enabled)
      if (mounted.current) setStatus(data)
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '切换失败')
    } finally {
      if (mounted.current) setToggling(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void refresh()
    return () => {
      mounted.current = false
    }
  }, [refresh])

  return { status, loading, toggling, error, refresh, toggle }
}
