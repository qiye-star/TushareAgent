import { useEffect, useState } from 'react'

/**
 * 图表主题 token：从 documentElement 的 computed style 读 --rt-chart-*（globals.css 里
 * :root + .dark 各定义一套）。
 *
 * 为什么不读 --color-zinc-400 / --color-primary-500：Tailwind v4 会裁剪未被任何工具类
 * 引用的 theme 变量（实测构建产物里只剩下真正用到的那些），且 Tailwind 的颜色变量在
 * .dark 下不变（dark: 变体是另写一条规则读同一个变量）——图表要在 JS 里读值，
 * 没有「暗色版」可读。所以调色板必须走 globals.css 里手写的 --rt-chart-*。
 *
 * dark 切换靠 MutationObserver 观察 documentElement 的 class（useTheme.ts 就是这么切的）。
 */
export interface ChartTokens {
  dark: boolean
  up: string
  down: string
  flat: string
  axis: string
  split: string
  text: string
  dim: string
  tipBg: string
  tipBorder: string
  tipText: string
  series: readonly string[]
  fontMono: string
}

const FALLBACK: ChartTokens = {
  dark: false,
  up: '#e11d48',
  down: '#059669',
  flat: '#71717a',
  axis: '#d4d4d8',
  split: '#f1f1f3',
  text: '#52525b',
  dim: '#a1a1aa',
  tipBg: '#ffffff',
  tipBorder: '#e4e4e7',
  tipText: '#27272a',
  series: ['#2563eb', '#60a5fa', '#1e40af', '#93c5fd', '#a1a1aa'],
  fontMono: 'ui-monospace, monospace',
}

function read(): ChartTokens {
  if (typeof document === 'undefined') return FALLBACK
  const root = document.documentElement
  const cs = getComputedStyle(root)
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  return {
    dark: root.classList.contains('dark'),
    up: v('--rt-chart-up', FALLBACK.up),
    down: v('--rt-chart-down', FALLBACK.down),
    flat: v('--rt-chart-flat', FALLBACK.flat),
    axis: v('--rt-chart-axis', FALLBACK.axis),
    split: v('--rt-chart-split', FALLBACK.split),
    text: v('--rt-chart-text', FALLBACK.text),
    dim: v('--rt-chart-dim', FALLBACK.dim),
    tipBg: v('--rt-chart-tip-bg', FALLBACK.tipBg),
    tipBorder: v('--rt-chart-tip-border', FALLBACK.tipBorder),
    tipText: v('--rt-chart-tip-text', FALLBACK.tipText),
    series: [1, 2, 3, 4, 5].map((i, k) => v(`--rt-chart-s${i}`, FALLBACK.series[k]!)),
    fontMono: v('--font-mono', FALLBACK.fontMono),
  }
}

function same(a: ChartTokens, b: ChartTokens): boolean {
  return a.dark === b.dark && a.up === b.up && a.axis === b.axis && a.tipBg === b.tipBg && a.series[0] === b.series[0]
}

export function useChartTokens(): ChartTokens {
  const [tokens, setTokens] = useState<ChartTokens>(read)
  useEffect(() => {
    setTokens((p) => {
      const n = read()
      return same(p, n) ? p : n
    })
    const obs = new MutationObserver(() =>
      setTokens((p) => {
        const n = read()
        return same(p, n) ? p : n
      }),
    )
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return tokens
}
