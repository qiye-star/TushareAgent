import type { ChartTokens } from '@/hooks/useChartTokens'
import type { ChartOption } from '@/components/charts/echarts-setup'
import type { PctBucket } from '@/lib/reportMetrics'

/**
 * 标的池当日涨跌分布：固定 8+1 档桶的横向堆叠单行条（heat strip）。
 * 桶色从 down(emerald) 经 flat(zinc) 到 up(rose) 离散取值（不是连续 visualMap，
 * 因为「恰好持平」必须是中性灰而不是渐变中点）。
 *
 * 纯前端从 watchlist.rows 算（lib/reportMetrics.ts::computeReportMetrics），
 * 不需要任何后端新字段，所以第一阶段就能上。
 */
function bucketColor(i: number, total: number, t: ChartTokens): string {
  const mid = Math.floor(total / 2)
  if (i === mid) return t.flat
  return i < mid ? t.down : t.up
}

export function pctDistributionOption(buckets: PctBucket[], t: ChartTokens): ChartOption {
  const total = buckets.reduce((a, b) => a + b.count, 0) || 1

  return {
    animation: false,
    grid: { top: 2, bottom: 2, left: 0, right: 0 },
    xAxis: { type: 'value', show: false, max: total },
    yAxis: { type: 'category', data: [''], show: false },
    tooltip: {
      trigger: 'item',
      confine: true,
      backgroundColor: t.tipBg,
      borderColor: t.tipBorder,
      borderWidth: 1,
      textStyle: { color: t.tipText, fontSize: 11 },
      formatter: (p) => {
        if (Array.isArray(p)) return ''
        const v = typeof p.value === 'number' ? p.value : 0
        const pct = total > 0 ? Math.round((v / total) * 1000) / 10 : 0
        return `${p.seriesName}：${v} 只（${pct}%）`
      },
    },
    series: buckets.map((b, i) => ({
      type: 'bar' as const,
      name: b.label,
      stack: 'dist',
      barWidth: 14,
      data: [b.count],
      itemStyle: {
        color: bucketColor(i, buckets.length, t),
        borderRadius: i === 0 ? [3, 0, 0, 3] : i === buckets.length - 1 ? [0, 3, 3, 0] : 0,
      },
      emphasis: { focus: 'series' as const },
    })),
  }
}
