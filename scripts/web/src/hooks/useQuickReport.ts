import { useCallback, useEffect, useRef, useState } from 'react'
import {
  generateQuickReport,
  getQuickReport,
  getQuickReportByDate,
  listQuickReportHistory,
  type QuickReportHistoryItem,
} from '@/lib/api'
import type { QuickReport } from '@/lib/quickReport'

/**
 * 快报数据加载（镜像 useSessions 的 useCallback + useState + 挂载 effect 模式）。
 * 快报是「单次 GET + 手动生成/刷新 + 历史回看」，无流式，故不用 Zustand store。
 * 提升到 App 层调用一次：Sidebar（历史列表）与 QuickReportView（正文）共享同一状态。
 *
 * - report：当前展示的快报（latest 或选中的历史）
 * - activeDate：当前展示的报告日（ISO），选中历史日期后与其对应；null = 未加载/无 latest
 * - histories：历史快报摘要（日期降序）
 * - selectHistory(day)：切换历史快报；refresh()：回最新
 */
export function useQuickReport() {
  const [report, setReport] = useState<QuickReport | null>(null)
  const [activeDate, setActiveDate] = useState<string | null>(null)
  const [histories, setHistories] = useState<QuickReportHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [data, history] = await Promise.all([getQuickReport(), listQuickReportHistory()])
      if (mounted.current) {
        setReport(data)
        setActiveDate(data?.date ?? null)
        setHistories(history)
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '网络异常')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  const selectHistory = useCallback(async (day: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getQuickReportByDate(day)
      if (mounted.current) {
        setReport(data)
        setActiveDate(data?.date ?? day)
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '网络异常')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [])

  const regenerate = useCallback(async () => {
    setGenerating(true)
    setError(null)
    try {
      const data = await generateQuickReport()
      const history = await listQuickReportHistory()
      if (mounted.current) {
        setReport(data)
        setActiveDate(data?.date ?? null)
        setHistories(history)
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      if (mounted.current) setGenerating(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void refresh()
    return () => {
      mounted.current = false
    }
  }, [refresh])

  return { report, activeDate, histories, loading, generating, error, refresh, selectHistory, regenerate }
}
