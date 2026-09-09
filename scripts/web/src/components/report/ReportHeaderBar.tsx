import { ArrowsClockwise, CircleNotch, ClockCounterClockwise } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { formatTime } from '@/lib/format'
import type { QuickReport } from '@/lib/quickReport'
import type { Freshness } from '@/lib/quickReportStatus'

type Props = {
  report: QuickReport
  activeDate: string | null
  isHistory: boolean
  generating: boolean
  freshness: Freshness
  hasNewerReport: boolean
  onBackToLatest: () => void
  onRegenerate: () => void
  onLoadNewer: () => void
}

const FRESHNESS_BADGE: Record<Freshness, { tone: 'success' | 'primary' | 'neutral' | 'error'; label: string } | null> = {
  fresh: { tone: 'success', label: '数据最新' },
  generating: { tone: 'primary', label: '生成中…' },
  failed: { tone: 'error', label: '上次生成失败' },
  stale: { tone: 'neutral', label: '数据可能过期' },
  'newer-available': null, // 单独渲染成可点击 pill
  unknown: null,
}

/** 区域 A：报告日 / 生成于 / 新鲜度 pill / 回到最新 / 刷新-重生成。 */
export function ReportHeaderBar({
  report,
  activeDate,
  isHistory,
  generating,
  freshness,
  hasNewerReport,
  onBackToLatest,
  onRegenerate,
  onLoadNewer,
}: Props) {
  const badge = FRESHNESS_BADGE[freshness]

  return (
    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-[var(--rt-line)] px-4 md:px-5">
      <span className="rt-label">报告日</span>
      <span className="rt-num rt-body font-medium">{activeDate ?? report.date}</span>
      {isHistory && (
        <Badge tone="primary">
          <ClockCounterClockwise size={11} weight="bold" />
          历史快报
        </Badge>
      )}
      <span className="hidden text-zinc-300 dark:text-zinc-700 sm:inline">·</span>
      <span className="hidden rt-micro sm:inline">生成于 {formatTime(report.generated_at)}</span>
      {!isHistory && badge && <Badge tone={badge.tone}>{badge.label}</Badge>}
      {!isHistory && !badge && hasNewerReport && (
        <button
          type="button"
          onClick={onLoadNewer}
          className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700 hover:bg-amber-200 dark:bg-amber-500/15 dark:text-amber-300 dark:hover:bg-amber-500/25"
        >
          有更新 · 载入
        </button>
      )}

      <span className="ml-auto flex items-center gap-2.5">
        {isHistory && (
          <Button size="sm" variant="ghost" onClick={onBackToLatest}>
            回到最新
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={onRegenerate} disabled={generating}>
          {generating ? (
            <>
              <CircleNotch size={13} className="animate-spin" />
              生成中…
            </>
          ) : (
            <>
              <ArrowsClockwise size={13} />
              刷新 / 重生成
            </>
          )}
        </Button>
      </span>
    </div>
  )
}
