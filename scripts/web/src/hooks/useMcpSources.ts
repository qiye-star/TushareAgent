import { useCallback, useEffect, useRef, useState } from 'react'
import { getMcpSources, setMcpSourceEnabled } from '@/lib/api'
import type { McpSource } from '@/lib/mcp'

/**
 * 网关按源开关状态（镜像 useMcpStatus.ts 的 loading/toggling/error 模式）。
 * 网关不可达/没配好不是错误态而是**要显示出来的运维态**：reachable/gatewayUrl/configProblems 一并暴露，
 * 调用方据此渲染诊断信息（区块本身恒定渲染，绝不因为拿不到源而整块隐藏）。
 * toggle(id, enabled) 只更新被切换的那一条，不重新整体 GET。
 */
export function useMcpSources() {
  const [sources, setSources] = useState<McpSource[]>([])
  const [gatewayUrl, setGatewayUrl] = useState('')
  const [reachable, setReachable] = useState(false)
  const [configProblems, setConfigProblems] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [togglingId, setTogglingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getMcpSources()
      if (mounted.current) {
        setGatewayUrl(data.gateway_url)
        setReachable(data.reachable)
        setConfigProblems(data.config_problems ?? [])
        setSources(data.sources)
        setError(data.error)
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '网络异常')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  const toggle = useCallback(async (id: string, enabled: boolean) => {
    setTogglingId(id)
    setError(null)
    try {
      const updated = await setMcpSourceEnabled(id, enabled)
      if (mounted.current) {
        setSources((prev) => prev.map((s) => (s.id === id ? updated : s)))
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '切换失败')
    } finally {
      if (mounted.current) setTogglingId(null)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void refresh()
    return () => {
      mounted.current = false
    }
  }, [refresh])

  return { sources, gatewayUrl, reachable, configProblems, loading, togglingId, error, refresh, toggle }
}
