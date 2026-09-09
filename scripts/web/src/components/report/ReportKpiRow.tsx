import { pctClass } from '@/lib/format'
import type { QuickReport } from '@/lib/quickReport'
import { computeReportMetrics } from '@/lib/reportMetrics'
import { KpiTile } from './KpiTile'

const SECTION_LABELS: Record<string, string> = {
  board: '板块',
  watchlist: '标的池',
  announce: '公告',
  forecast: '业绩预告',
  news: '催化事件',
}

/** 区域 B：5 块 KPI（窄屏横向滚动条 / md+ 五列网格）。 */
export function ReportKpiRow({ report }: { report: QuickReport }) {
  const m = computeReportMetrics(report)

  return (
    <div className="shrink-0 border-b border-[var(--rt-line)] px-4 py-2.5 md:px-5">
      <div className="flex gap-2 overflow-x-auto pb-1 md:grid md:grid-cols-5 md:gap-2.5 md:overflow-visible md:pb-0">
        <KpiTile
          label="池内涨跌"
          value={
            <>
              <span className="text-rose-600 dark:text-rose-400">{m.breadth.up}</span>
              <span className="mx-1 text-zinc-300 dark:text-zinc-700">/</span>
              <span className="text-emerald-600 dark:text-emerald-400">{m.breadth.down}</span>
            </>
          }
          footnote={
            m.breadthFullPool
              ? `平 ${m.breadth.flat} · 全池 ${m.poolSize} 只`
              : `平 ${m.breadth.flat} · 样本 ${m.sampleSize}/${m.poolSize}`
          }
        />
        <KpiTile
          label="平均涨跌幅"
          value={m.avgPct != null ? `${m.avgPct > 0 ? '+' : ''}${m.avgPct.toFixed(2)}%` : '—'}
          valueClassName={pctClass(m.avgPct)}
          footnote="等权算术平均"
        />
        <KpiTile
          label="标的池净流入"
          value={m.poolInflowText ?? '—'}
          valueClassName={pctClass(m.poolInflow)}
          footnote="全市场资金流快照汇总"
        />
        <KpiTile
          label="业绩预告异动"
          value={m.forecastAlerts}
          valueClassName={m.forecastAlerts > 0 ? 'text-primary-600 dark:text-primary-400' : undefined}
          footnote={`阈值 >+${report.forecast.thresholds.up}% / <${report.forecast.thresholds.down}%`}
        />
        <KpiTile
          label="数据源接入"
          value={`${m.sectionsWired}/5`}
          valueClassName={m.sectionsNa === 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}
          footnote={
            report.missing.length > 0
              ? `未接入：${report.missing.map((k) => SECTION_LABELS[k] ?? k).join('、')}`
              : '五段全部接入'
          }
        />
      </div>
    </div>
  )
}
