import { useMemo } from 'react'
import { ArrowsClockwise, CircleNotch, FileText } from '@phosphor-icons/react'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import type { McpSource } from '@/lib/mcp'
import type { QuickReport } from '@/lib/quickReport'
import type { Freshness, QuickReportStatus } from '@/lib/quickReportStatus'
import { computeReportMetrics } from '@/lib/reportMetrics'
import { ReportHeaderBar } from './ReportHeaderBar'
import { ReportKpiRow } from './ReportKpiRow'
import { PanelAnnounce } from './panels/PanelAnnounce'
import { PanelBrief } from './panels/PanelBrief'
import { PanelDataSource } from './panels/PanelDataSource'
import { PanelForecast } from './panels/PanelForecast'
import { PanelIndexTrend } from './panels/PanelIndexTrend'
import { PanelNews } from './panels/PanelNews'
import { PanelPoolFlow } from './panels/PanelPoolFlow'
import { PanelWatchlist } from './panels/PanelWatchlist'

/**
 * 快报仪表盘（主区）。布局三段：
 *   A 头部条（报告日/生成于/新鲜度/刷新）—— shrink-0
 *   B KPI 指标行 —— shrink-0
 *   C 左右分栏 body —— grid，min-h-0 flex-1
 *
 * **min-h-0 / overflow 链是这套布局的关键**（错一处就静默退化）：
 * - C 是 flex 列的 grid 子项，需要 `min-h-0 flex-1`；
 * - **grid 子项默认 min-height:auto**，所以 C-L / C-R 各自还要 `min-h-0`，
 *   漏了就会退化成整页一个滚动条、两栏独立滚动静默失效；
 * - overflow 归属在 md 断点**换手**：<md 由 C 滚（单列），≥md 由两栏各自滚（C 自己 hidden）。
 *   两半必须成对出现，验收时卡 767px / 768px 两个点。
 */
type Props = {
  report: QuickReport | null
  activeDate: string | null
  loading: boolean
  generating: boolean
  error: string | null
  status: QuickReportStatus | null
  freshness: Freshness
  hasNewerReport: boolean
  mcpSources: McpSource[]
  onRefresh: () => void
  onRegenerate: () => void
  onBackToLatest: () => void
  onGoToSettings: () => void
}

export function QuickReportView({
  report,
  activeDate,
  loading,
  generating,
  error,
  status,
  freshness,
  hasNewerReport,
  mcpSources,
  onRefresh,
  onRegenerate,
  onBackToLatest,
  onGoToSettings,
}: Props) {
  const isHistory = !!activeDate && !!report && activeDate !== report.date
  const metrics = useMemo(() => (report ? computeReportMetrics(report) : null), [report])

  // —— 无报文的三种状态：加载 / 出错 / 未生成 ——
  if (!report) {
    return (
      <div className="report-dense flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 md:px-8">
          <div className="mx-auto max-w-2xl space-y-3">
            {loading && (
              <>
                <Skeleton className="h-11 w-full rounded-card" />
                <Skeleton className="h-20 w-full rounded-card" />
                <Skeleton className="h-64 w-full rounded-card" />
              </>
            )}
            {error && !loading && (
              <div className="rounded-card border border-rose-200 bg-rose-50/50 px-4 py-6 text-center dark:border-rose-500/30 dark:bg-rose-500/5">
                <p className="rt-body text-rose-600 dark:text-rose-400">{error}</p>
                <Button size="sm" variant="outline" className="mt-3" onClick={onRefresh}>
                  重试
                </Button>
              </div>
            )}
            {!error && !loading && (
              <div className="rounded-card border border-dashed border-[var(--rt-line)] px-6 py-12 text-center">
                <FileText size={36} className="mx-auto text-zinc-300 dark:text-zinc-600" weight="duotone" />
                <p className="mt-3 rt-lede font-bold">快报尚未生成</p>
                <p className="rt-micro mx-auto mt-1 max-w-sm">
                  生成后可在每个交易日前查阅六段式产业链高频跟踪（板块 / 标的池 / 公告 / 业绩异动 / 催化事件 / 研判），
                  数据为确定性计算，不经过 LLM 判断。
                </p>
                <Button size="sm" className="mt-4" onClick={onRegenerate} disabled={generating}>
                  {generating ? <CircleNotch size={13} className="animate-spin" /> : <ArrowsClockwise size={13} />}
                  生成快报
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="report-dense flex min-h-0 flex-1 flex-col">
      {/* A */}
      <ReportHeaderBar
        report={report}
        activeDate={activeDate}
        isHistory={isHistory}
        generating={generating}
        freshness={freshness}
        hasNewerReport={hasNewerReport}
        onBackToLatest={onBackToLatest}
        onRegenerate={onRegenerate}
        onLoadNewer={onRefresh}
      />

      {/* B */}
      <ReportKpiRow report={report} />

      {/* C —— 断点用 lg(1024) 而不是 md(768)：左侧 Sidebar 恒占 256px、ConfigPanel 打开再占 320px，
          实测 vw=768 时留给这个 grid 只有 478px，右栏 clamp 下限 19rem(304px) 会把「主区」挤成 174px
          （36% / 64%，主次颠倒）。改到 lg 且把下限收到 17rem 后：vw=1024 → 462/272，vw=1280 → 718/272。
          **overflow 归属的换手断点必须与列定义同一个**，否则会出现嵌套双滚动条。 */}
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_clamp(17rem,24vw,22rem)] lg:overflow-hidden">
        {/* C-L 左主区 */}
        <section className="flex min-h-0 min-w-0 flex-col gap-2.5 px-4 py-3.5 lg:overflow-y-auto lg:px-5 lg:py-4">
          <PanelIndexTrend section={report.board} />
          <PanelPoolFlow section={report.board} />
          <PanelWatchlist section={report.watchlist} buckets={metrics?.buckets ?? []} />
          <PanelAnnounce section={report.announce} />
        </section>

        {/* C-R 右侧栏 */}
        <aside className="flex min-h-0 min-w-0 flex-col gap-2.5 border-t border-[var(--rt-line)] px-4 pb-6 pt-3.5 lg:border-l lg:border-t-0 lg:overflow-y-auto lg:px-4 lg:py-4">
          {/* 一句话研判信息密度最高 → 单列时提到最前面 */}
          <div className="max-lg:order-first">
            <PanelBrief brief={report.brief} />
          </div>
          <PanelForecast section={report.forecast} />
          <PanelNews section={report.news} />
          <PanelDataSource
            report={report}
            status={status}
            mcpSources={mcpSources}
            isHistory={isHistory}
            onGoToSettings={onGoToSettings}
          />
          <p className="rt-micro px-0.5">
            本快报由结构化数据自动生成，仅供研究参考，不构成任何投资建议；所有数据以官方披露为准。
          </p>
        </aside>
      </div>
    </div>
  )
}

export type QuickReportViewProps = Props
