import type { ChartTokens } from '@/hooks/useChartTokens'
import type { ChartOption } from '@/components/charts/echarts-setup'
import type { TopInflowItem } from '@/lib/quickReport'

/**
 * 资金净流入 TOP5：横向条形，yAxis.inverse 让 rank1 在最上。逐条按符号取色
 * （净流入=rose「涨」/ 净流出=emerald「跌」）。
 *
 * 这是对 ReportSections.tsx 里那个手写 CSS 宽度条的正确替代：旧实现
 * `Math.max(6, (inflow/maxTop)*100)` 配合无条件 `bg-rose-100`，会让**净流出**也画出一条
 * 6% 宽的红色（=涨）条——符号信息在视觉上完全丢失。这里按符号取色，不会犯同样的错。
 */
export function poolFlowOption(items: TopInflowItem[], t: ChartTokens): ChartOption {
  const ordered = [...items].reverse() // inverse category 轴：数组末位在最上
  const names = ordered.map((i) => i.name)
  const values = ordered.map((i) => i.inflow ?? 0)

  return {
    animation: true,
    animationDuration: 240,
    grid: { top: 4, bottom: 4, left: 2, right: 46, containLabel: true },
    xAxis: { type: 'value', show: false },
    yAxis: {
      type: 'category',
      inverse: true,
      data: names,
      axisLabel: { color: t.text, fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    tooltip: {
      trigger: 'item',
      confine: true,
      backgroundColor: t.tipBg,
      borderColor: t.tipBorder,
      borderWidth: 1,
      textStyle: { color: t.tipText, fontSize: 11 },
    },
    series: [
      {
        type: 'bar',
        barWidth: 12,
        itemStyle: {
          borderRadius: [0, 3, 3, 0],
          color: (params) => {
            const v = typeof params.value === 'number' ? params.value : 0
            return v >= 0 ? t.up : t.down
          },
        },
        data: values,
        label: {
          show: true,
          position: 'right',
          formatter: (p) => {
            const v = typeof p.value === 'number' ? p.value : 0
            return `${v >= 0 ? '+' : ''}${v.toFixed(2)}`
          },
          color: t.text,
          fontSize: 10.5,
          fontFamily: t.fontMono,
        },
      },
    ],
  }
}
