import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  generateQuickReport,
  getQuickReport,
  getQuickReportByDate,
  getQuickReportStatus,
  listQuickReportHistory,
  type QuickReportHistoryItem,
} from '@/lib/api'
import type { QuickReport } from '@/lib/quickReport'
import { deriveFreshness, type Freshness, type QuickReportStatus } from '@/lib/quickReportStatus'

/**
 * 快报数据加载（镜像 useSessions 的 useCallback + useState + 挂载 effect 模式）。
 * 提升到 App 层调用一次：Sidebar（历史列表）与 QuickReportView（正文）共享同一状态。
 *
 * 轮询五条规则：
 * ① **绝不轮询报文本身**（latest.json 二三百 KB，加上走势序列更大）——只轮询廉价的 /status，
 *    检测到 generated_at 变了才去拉报文；
 * ② 可见性感知：document.hidden 时不轮询，visibilitychange→visible 立刻补一次
 *    （标签页开着过夜不该发出上千个请求，但被看到的那一秒必须是准的）；
 * ③ 自适应间隔：常态 60s、生成中 5s、**看历史报文时完全停摆**（历史是不可变的，
 *    轮询只会跟用户「给我看上周二」的明确意图打架）；
 * ④ 用**自续期 setTimeout** 而非 setInterval：间隔会在 60s/5s 之间变，
 *    setInterval 在 effect 里按 delay 重建会双触发；
 * ⑤ **不把用户正在读的内容换掉**：检测到磁盘上有更新只置 hasNewerReport 亮一个可点 pill，
 *    唯一例外是 running: true→false（那正是用户/日程明确要的结果，自动拉一次）。
 */
const POLL_IDLE = 60_000
const POLL_BUSY = 5_000

export function useQuickReport() {
  const [report, setReport] = useState<QuickReport | null>(null)
  const [activeDate, setActiveDate] = useState<string | null>(null)
  const [histories, setHistories] = useState<QuickReportHistoryItem[]>([])
  const [status, setStatus] = useState<QuickReportStatus | null>(null)
  const [hasNewerReport, setHasNewerReport] = useState(false)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mounted = useRef(true)
  const reportGenRef = useRef<string | null>(null) // 当前已加载报文的 generated_at
  const wasRunningRef = useRef(false)
  const timerRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [data, history] = await Promise.all([getQuickReport(), listQuickReportHistory()])
      if (mounted.current) {
        setReport(data)
        setActiveDate(data?.date ?? null)
        setHistories(history)
        reportGenRef.current = data?.generated_at ?? null
        setHasNewerReport(false)
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
        reportGenRef.current = data?.generated_at ?? null
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
        reportGenRef.current = data?.generated_at ?? null
        setHasNewerReport(false)
      }
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '生成失败')
    } finally {
      if (mounted.current) setGenerating(false)
    }
  }, [])

  const refreshStatus = useCallback(async () => {
    try {
      const s = await getQuickReportStatus()
      if (!mounted.current) return
      setStatus(s)

      // running true→false：生成刚结束，自动拉一次（这正是用户/日程要的结果）
      const busy = s?.running === true
      if (wasRunningRef.current && !busy) void refresh()
      wasRunningRef.current = busy

      // 磁盘上有更新的报文：只亮 pill，不替换正在读的内容
      let remoteGen = s?.generatedAt ?? null
      if (remoteGen === null) {
        // /status 不可用（旧后端 404）→ 退化用 history 列表检测，零后端改动也能有「有更新」能力
        const hist = await listQuickReportHistory().catch(() => [])
        if (!mounted.current) return
        remoteGen = hist[0]?.generated_at ?? null
      }
      if (remoteGen && reportGenRef.current && remoteGen !== reportGenRef.current) {
        setHasNewerReport(true)
      }
    } catch {
      /* 轮询失败静默：不把网络抖动变成正文的 error 态（已渲染的报文仍然有效） */
    }
  }, [refresh])

  // 单个自续期 timeout：可变间隔 + 可见性感知 + 历史态停摆
  const isHistoryView = !!activeDate && !!report && activeDate !== report.date
  const busy = status?.running === true || generating
  useEffect(() => {
    if (isHistoryView) return

    const tick = async () => {
      if (!document.hidden) await refreshStatus()
      if (!mounted.current) return
      timerRef.current = window.setTimeout(tick, busy ? POLL_BUSY : POLL_IDLE)
    }
    timerRef.current = window.setTimeout(tick, busy ? POLL_BUSY : POLL_IDLE)

    const onVis = () => {
      if (!document.hidden) void refreshStatus()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [refreshStatus, isHistoryView, busy])

  useEffect(() => {
    mounted.current = true
    void refresh()
    void refreshStatus()
    return () => {
      mounted.current = false
    }
  }, [refresh, refreshStatus])

  const freshness: Freshness = useMemo(
    () =>
      deriveFreshness({
        status,
        localGenerating: generating,
        hasNewerReport,
        isHistory: isHistoryView,
      }),
    [status, generating, hasNewerReport, isHistoryView],
  )

  return {
    report,
    activeDate,
    histories,
    status,
    freshness,
    hasNewerReport,
    loading,
    generating,
    error,
    refresh,
    refreshStatus,
    selectHistory,
    regenerate,
  }
}
