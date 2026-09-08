import { ArrowsClockwise, ClockCounterClockwise, CircleNotch, FileText } from '@phosphor-icons/react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { formatTime } from '@/lib/format'
import type { QuickReport } from '@/lib/quickReport'
import { SectionAnnounce, SectionBoard, SectionBrief, SectionForecast, SectionNews, SectionWatchlist } from './ReportSections'

/**
 * 快报视图（主区）：滚动容器 + 信息行（报告日/更新时间/刷新，历史回看时附「回到最新」）
 * + 六段卡片 + 元信息提示。状态由 App 层 useQuickReport 提供（Sidebar 的历史列表共用）。
 */
type Props = {
  report: QuickReport | null
  activeDate: string | null
  loading: boolean
  generating: boolean
  error: string | null
  onRefresh: () => void
  onRegenerate: () => void
  onBackToLatest: () => void
}

export function QuickReportView({
  report,
  activeDate,
  loading,
  generating,
  error,
  onRefresh,
  onRegenerate,
  onBackToLatest,
}: Props) {
  const isHistory = !!activeDate && !!report && activeDate !== report.date
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 md:px-8">
        <div className="mx-auto max-w-3xl space-y-4">
          {/* 信息行：日期 / 生成时间 / 刷新 */}
          <div className="flex items-center gap-2">
            <FileText size={15} className="text-primary-600 dark:text-primary-400" weight="duotone" />
            {report && <Badge tone="neutral">报告日 {activeDate ?? report.date}</Badge>}
            {isHistory && (
              <Badge tone="primary">
                <ClockCounterClockwise size={11} weight="bold" />
                历史快报
              </Badge>
            )}
            <span className="ml-auto flex items-center gap-3">
              {report && (
                <span className="hidden text-[11px] text-zinc-400 sm:inline dark:text-zinc-500">
                  更新于 {formatTime(report.generated_at)}
                </span>
              )}
              {isHistory && (
                <Button size="sm" variant="ghost" onClick={onBackToLatest}>
                  回到最新
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={() => void onRegenerate()} disabled={generating}>
                {generating ? (
                  <>
                    <CircleNotch size={14} className="animate-spin" />
                    生成中…
                  </>
                ) : (
                  <>
                    <ArrowsClockwise size={14} />
                    刷新 / 重生成
                  </>
                )}
              </Button>
            </span>
          </div>
          {loading && !report && (
            <div className="space-y-4">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-24 animate-pulse rounded-xl bg-zinc-100 dark:bg-zinc-800/60" />
              ))}
            </div>
          )}

          {error && !report && (
            <div className="rounded-xl border border-rose-200 bg-rose-50/50 px-4 py-6 text-center dark:border-rose-500/30 dark:bg-rose-500/5">
              <p className="text-[13px] text-rose-600 dark:text-rose-400">{error}</p>
              <Button size="sm" variant="outline" className="mt-3" onClick={onRefresh}>
                重试
              </Button>
            </div>
          )}

          {!error && !loading && !report && (
            <div className="rounded-xl border border-dashed border-zinc-300 bg-white px-6 py-12 text-center dark:border-zinc-700 dark:bg-zinc-900">
              <FileText size={36} className="mx-auto text-zinc-300 dark:text-zinc-600" weight="duotone" />
              <p className="mt-3 text-sm font-medium text-zinc-700 dark:text-zinc-200">快报尚未生成</p>
              <p className="mx-auto mt-1 max-w-sm text-[12.5px] text-zinc-400 dark:text-zinc-500">
                生成后可在每个交易日前查阅六段式产业链高频跟踪（板块 / 标的池 / 公告 / 业绩异动 / 催化事件 / 研判），
                数据为确定性计算，不经过 LLM 判断。
              </p>
              <Button size="sm" className="mt-4" onClick={() => void onRegenerate()} disabled={generating}>
                {generating ? <CircleNotch size={14} className="animate-spin" /> : <ArrowsClockwise size={14} />}
                生成快报
              </Button>
            </div>
          )}

          {report && (
            <>
              {/* 元信息提示行：段缺失（未接入）与生成错误 */}
              {(report.missing.length > 0 || report.errors.length > 0) && (
                <div className="rounded-lg border border-amber-200/70 bg-amber-50/60 px-3 py-2 dark:border-amber-500/20 dark:bg-amber-500/5">
                  <p className="text-[11.5px] text-amber-700 dark:text-amber-400">
                    {report.missing.length > 0 && (
                      <span className="mr-2">
                        数据未接入：{report.missing.map((m) => SECTION_LABELS[m] ?? m).join('、')}
                      </span>
                    )}
                    {report.errors.length > 0 && <span className="opacity-70">生成过程有 {report.errors.length} 处取数告警</span>}
                  </p>
                </div>
              )}
              <SectionBoard section={report.board} />
              <SectionWatchlist section={report.watchlist} />
              <SectionAnnounce section={report.announce} />
              <SectionForecast section={report.forecast} />
              <SectionNews section={report.news} />
              <SectionBrief brief={report.brief} />
              <p className="px-1 pt-1 text-[11px] text-zinc-400 dark:text-zinc-600">
                本快报由结构化数据自动生成，仅供研究参考，不构成任何投资建议；所有数据以官方披露为准。
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const SECTION_LABELS: Record<string, string> = {
  board: '板块概览',
  watchlist: '标的池行情',
  announce: '关键公告',
  forecast: '业绩预告异动',
  news: '催化事件',
}

/** useQuickReport 返回 null 时的组件接口（占位，保留导出以兼容类型推导）。 */
export type QuickReportViewProps = Props
