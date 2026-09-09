import { lazy, Suspense, useMemo } from 'react'
import { TrendUp } from '@phosphor-icons/react'
import { Skeleton } from '@/components/ui/Skeleton'
import { useChartTokens } from '@/hooks/useChartTokens'
import { pctClass } from '@/lib/format'
import type { BoardSection } from '@/lib/quickReport'
import { indexTrendOption } from '../charts/indexTrendOption'
import { PanelShell } from '../PanelShell'

const EChart = lazy(() => import('@/components/charts/EChart'))

/**
 * 指数走势（左主区 1）：多序列归一化折线 + 当日各指数涨跌 strip。
 *
 * 序列为空（后端未配 board.series，或该段取数失败）时**不挂载 EChart**——
 * ECharts 的空图是一个坐标轴十字，读起来像坏了，不如直接给一行说明。
 */
export function PanelIndexTrend({ section }: { section: BoardSection }) {
  const t = useChartTokens()
  const series = section.series
  const option = useMemo(() => indexTrendOption(series, t), [series, t])
  const chartable = series.length > 0 && series.some((s) => s.points.length >= 2)

  return (
    <PanelShell
      icon={TrendUp}
      title="指数走势"
      status={section.status}
      note={section.note}
      hint={
        chartable ? (
          <span className="rt-micro">相对首日累计涨跌 · Shift+滚轮缩放</span>
        ) : undefined
      }
    >
      {/* 当日各指数涨跌：密排 strip，替代原来的两列小表格 */}
      {section.rows.length > 0 && (
        <div className="mb-2.5 flex flex-wrap gap-x-4 gap-y-1">
          {section.rows.map((r) => (
            <span key={r.name} className="inline-flex items-baseline gap-1.5">
              <span className="rt-body">{r.name}</span>
              <span className={`rt-num rt-body font-bold ${pctClass(r.pct)}`}>{r.pct_text ?? '—'}</span>
            </span>
          ))}
        </div>
      )}

      {chartable ? (
        <Suspense fallback={<Skeleton className="h-[200px] w-full rounded-control md:h-[248px]" />}>
          <EChart
            option={option}
            className="h-[200px] md:h-[248px]"
            ariaLabel={`指数走势折线图，包含 ${series.map((s) => s.name).join('、')}`}
          />
        </Suspense>
      ) : (
        <div className="rounded-control border border-dashed border-[var(--rt-line)] px-3 py-4 text-center">
          <p className="rt-micro">
            {section.seriesStatus === 'na'
              ? '走势序列未接入（在 watchlist.json 的 board.series 里配置指数即可启用）'
              : '走势序列数据不足'}
          </p>
        </div>
      )}
    </PanelShell>
  )
}
