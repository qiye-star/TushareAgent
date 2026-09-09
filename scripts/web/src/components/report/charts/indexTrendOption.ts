import type { ChartTokens } from '@/hooks/useChartTokens'
import type { ChartOption } from '@/components/charts/echarts-setup'
import type { IndexSeries } from '@/lib/quickReport'

/** tooltip.formatter 返回字符串会注入裸 HTML（ECharts 的 tooltip 不像 react-markdown
 *  那样天然免疫 XSS）；指数名来自后端 JSON，必须转义。 */
function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
}

/**
 * 指数走势：多序列折线。**纵轴是相对首日的累计涨跌幅（%），不是收盘价**——
 * 上证 ~3900 与创业板指 ~3400 放同一价格轴时可比性为零，归一化后 y=0 的 markLine
 * 就是「与首日持平」。
 *
 * x 轴用 category 而非 time：time 轴会把周末/假日画成空白间隔，A 股走势图惯例不留空；
 * 各序列缺失日填 null + connectNulls，序列长度不必一致（不同源可能起点不同）。
 */
export function indexTrendOption(series: IndexSeries[], t: ChartTokens): ChartOption {
  const unionDates = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.date)))).sort()
  const dateIndex = new Map(unionDates.map((d, i) => [d, i]))

  const lineSeries = series.map((s, i) => {
    const base = s.points[0]?.close ?? null
    const data: (number | null)[] = new Array(unionDates.length).fill(null)
    for (const p of s.points) {
      const idx = dateIndex.get(p.date)
      if (idx === undefined || base === null || base === 0 || p.close === null) continue
      data[idx] = Math.round(((p.close - base) / base) * 100 * 100) / 100
    }
    return {
      type: 'line' as const,
      name: s.name,
      data,
      showSymbol: false,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 1.5 },
      color: t.series[i % t.series.length],
      connectNulls: true,
      sampling: 'lttb' as const,
      emphasis: { focus: 'series' as const },
      ...(i === 0
        ? {
            markLine: {
              silent: true,
              symbol: 'none' as const,
              data: [{ yAxis: 0 }],
              lineStyle: { color: t.axis, type: 'dashed' as const, width: 1 },
              label: { show: false },
            },
          }
        : {}),
    }
  })

  return {
    animation: true,
    animationDuration: 240,
    grid: { top: 22, right: 8, bottom: 4, left: 4, containLabel: true },
    legend: {
      top: 0,
      right: 0,
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 10,
      icon: 'roundRect',
      textStyle: { color: t.text, fontSize: 11 },
    },
    xAxis: {
      type: 'category',
      data: unionDates,
      boundaryGap: false,
      axisLabel: { color: t.dim, fontSize: 10, hideOverlap: true, formatter: (d: string) => d.slice(5) },
      axisLine: { lineStyle: { color: t.axis } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: { color: t.dim, fontSize: 10, formatter: '{value}%' },
      splitLine: { lineStyle: { color: t.split } },
      axisLine: { show: false },
    },
    tooltip: {
      trigger: 'axis',
      confine: true, // 面板 overflow-hidden + 圆角，不 confine 会被裁掉
      axisPointer: { type: 'line', lineStyle: { color: t.axis } },
      backgroundColor: t.tipBg,
      borderColor: t.tipBorder,
      borderWidth: 1,
      textStyle: { color: t.tipText, fontSize: 11 },
      extraCssText: 'border-radius:7px;box-shadow:0 8px 24px -12px rgb(37 99 235 / .22);padding:7px 9px;',
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params]
        if (arr.length === 0) return ''
        const date = String(arr[0]?.name ?? '')
        const rows = arr
          .filter((p) => p.value !== null && p.value !== undefined)
          .map((p) => ({ name: String(p.seriesName ?? ''), value: Number(p.value) }))
          .sort((a, b) => b.value - a.value)
        const lines = rows
          .map((r) => {
            const color = r.value > 0 ? t.up : r.value < 0 ? t.down : t.flat
            const sign = r.value > 0 ? '+' : ''
            return `<div style="display:flex;justify-content:space-between;gap:12px;"><span>${esc(r.name)}</span><span style="color:${color};font-variant-numeric:tabular-nums;">${sign}${r.value.toFixed(2)}%</span></div>`
          })
          .join('')
        return `<div style="font-weight:700;margin-bottom:4px;">${esc(date)}</div>${lines}`
      },
    },
    dataZoom: [{ type: 'inside', throttle: 60, zoomOnMouseWheel: 'shift', moveOnMouseWheel: false }],
    series: lineSeries,
  }
}
