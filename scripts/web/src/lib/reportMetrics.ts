import type { QuickReport } from './quickReport'

/**
 * 纯函数派生层：从 QuickReport 算 KPI 行 + 涨跌分布桶。不含 React、不含格式化字符串
 *（格式化交给调用组件，用 lib/format.ts 的既有函数），只做数值计算，方便单独复用/测试。
 */

export interface PctBucket {
  label: string
  count: number
}

export interface ReportMetrics {
  breadth: { up: number; down: number; flat: number }
  avgPct: number | null
  /** true = breadth/avgPct 来自后端 watchlist.stats（全池口径）；
   *  false = 前端从 rows 自算（只有 display_limit 截断后的样本），UI 必须标注口径。 */
  breadthFullPool: boolean
  sampleSize: number
  poolSize: number
  poolInflow: number | null
  poolInflowText: string | null
  forecastAlerts: number
  announceCount: number
  newsCount: number
  /** 已接入的段数（status 非 na）。**刻意不是「status==='ok' 的段数」**：
   *  `empty` 表示「链路通、本期确实没有」——业绩预告在披露淡季合法为空，
   *  把它算成缺口会让「数据完整度」在一切正常的日子里长期显示 4/5。 */
  sectionsWired: number
  sectionsNa: number
  buckets: PctBucket[]
}

const BUCKET_DEFS: { label: string; min: number; max: number }[] = [
  { label: '≤-7%', min: -Infinity, max: -7 },
  { label: '-7~-3%', min: -7, max: -3 },
  { label: '-3~-1%', min: -3, max: -1 },
  { label: '-1~0%', min: -1, max: 0 },
  { label: '0%', min: 0, max: 0 },
  { label: '0~1%', min: 0, max: 1 },
  { label: '1~3%', min: 1, max: 3 },
  { label: '3~7%', min: 3, max: 7 },
  { label: '≥7%', min: 7, max: Infinity },
]

/** 涨跌分布桶：纯前端从 rows 算，不需要任何后端新字段，所以任何阶段都能上。
 *  「恰好 0」单独一档（中性灰），不落进 0~1% 或 -1~0% 里稀释掉。 */
function computeBuckets(pcts: number[]): PctBucket[] {
  const counts = new Array(BUCKET_DEFS.length).fill(0)
  for (const p of pcts) {
    if (p === 0) {
      counts[4]++
      continue
    }
    for (let i = 0; i < BUCKET_DEFS.length; i++) {
      if (i === 4) continue // 0% 档只用精确匹配
      const b = BUCKET_DEFS[i]
      if (p > b.min && p <= b.max) {
        counts[i]++
        break
      }
    }
  }
  return BUCKET_DEFS.map((b, i) => ({ label: b.label, count: counts[i] }))
}

export function computeReportMetrics(r: QuickReport): ReportMetrics {
  const stats = r.watchlist.stats
  const rows = r.watchlist.rows
  const samplePcts = rows.flatMap((row) => (row.pct != null ? [row.pct] : []))

  const breadthFullPool = stats !== null
  const breadth = stats
    ? { up: stats.up, down: stats.down, flat: stats.flat }
    : {
        up: samplePcts.filter((p) => p > 0).length,
        down: samplePcts.filter((p) => p < 0).length,
        flat: samplePcts.filter((p) => p === 0).length,
      }
  const avgPct = stats
    ? stats.avgPct
    : samplePcts.length
      ? Math.round((samplePcts.reduce((a, b) => a + b, 0) / samplePcts.length) * 100) / 100
      : null
  const sampleSize = stats ? stats.computedOver : samplePcts.length

  const sections = [r.board.status, r.watchlist.status, r.announce.status, r.forecast.status, r.news.status]

  return {
    breadth,
    avgPct,
    breadthFullPool,
    sampleSize,
    poolSize: r.watchlist.pool_count,
    poolInflow: r.board.pool_inflow?.value ?? null,
    poolInflowText: r.board.pool_inflow?.text ?? null,
    forecastAlerts: r.forecast.hit_count,
    announceCount: r.announce.items.length,
    newsCount: r.news.items.length,
    sectionsWired: sections.filter((s) => s !== 'na').length,
    sectionsNa: sections.filter((s) => s === 'na').length,
    buckets: computeBuckets(samplePcts),
  }
}
