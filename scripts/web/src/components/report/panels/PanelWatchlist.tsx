import { lazy, Suspense, useMemo, useState } from 'react'
import { Lightning, MagnifyingGlass } from '@phosphor-icons/react'
import { Skeleton } from '@/components/ui/Skeleton'
import { useChartTokens } from '@/hooks/useChartTokens'
import type { WatchlistSection } from '@/lib/quickReport'
import type { PctBucket } from '@/lib/reportMetrics'
import { pctDistributionOption } from '../charts/pctDistributionOption'
import { PctCell, capCls, openEastmoneyNotices, turnoverCls } from '../cells'
import { PanelShell } from '../PanelShell'
import { ReportTable } from '../ReportTable'

const EChart = lazy(() => import('@/components/charts/EChart'))

type WatchSortKey = 'pct' | 'close' | 'week_pct' | 'turnover' | 'market_cap' | null

/**
 * 标的池行情（左主区 3）：涨跌分布 strip + 搜索 + 三态排序表。
 * 搜索/排序逻辑从旧 SectionWatchlist 逐字节搬来（含「首点降序→再点升序→三点清除」的金融表惯例）。
 */
export function PanelWatchlist({ section, buckets }: { section: WatchlistSection; buckets: PctBucket[] }) {
  const t = useChartTokens()
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<WatchSortKey>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const distOption = useMemo(() => pctDistributionOption(buckets, t), [buckets, t])
  const hasDist = buckets.some((b) => b.count > 0)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return section.rows
    return section.rows.filter((r) => r.name.toLowerCase().includes(q) || r.ts_code.toLowerCase().includes(q))
  }, [section.rows, query])

  const sorted = useMemo(() => {
    if (!sortKey) return filtered
    const dir = sortDir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      return (av - bv) * dir
    })
  }, [filtered, sortKey, sortDir])

  const handleSort = (key: Exclude<WatchSortKey, null>) => {
    if (sortKey !== key) {
      setSortKey(key)
      setSortDir('desc')
    } else if (sortDir === 'desc') {
      setSortDir('asc')
    } else {
      setSortKey(null)
    }
  }
  const sortDirOf = (key: Exclude<WatchSortKey, null>) => (sortKey === key ? sortDir : null)

  // 展示口径脚注：row_count 是**截断前**的全池数，用 rows.length 说「返回多少」才不说谎
  //（旧实现用 row_count 渲染成「共 211 只，返回 211 只」，而载荷里只有 100 行）
  const footer =
    section.pool_count > 0
      ? `全池 ${section.pool_count} 只，本页 ${section.rows.length} 只${
          section.missing_codes > 0 ? `（缺失 ${section.missing_codes}）` : ''
        } · 点击行跳转个股公告页`
      : undefined

  return (
    <PanelShell
      icon={Lightning}
      title="标的池行情"
      status={section.status}
      note={section.note}
      hint={
        section.stats ? (
          <span className="rt-micro">
            全池 {section.stats.up} 涨 / {section.stats.down} 跌 · 中位 {section.stats.medianPct?.toFixed(2) ?? '—'}%
          </span>
        ) : undefined
      }
    >
      {hasDist && (
        <div className="mb-2.5">
          <Suspense fallback={<Skeleton className="h-[52px] w-full rounded-control" />}>
            <EChart
              option={distOption}
              className="h-[52px]"
              ariaLabel={`标的池涨跌分布：${buckets.map((b) => `${b.label} ${b.count} 只`).join('，')}`}
            />
          </Suspense>
        </div>
      )}

      <div className="mb-2 flex items-center gap-2">
        <div className="relative max-w-[220px] flex-1">
          <MagnifyingGlass
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400 dark:text-zinc-500"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索标的名称 / 代码"
            className="w-full rounded-control border border-zinc-300 bg-white py-1.5 pl-7 pr-2.5 text-[12px] text-zinc-800 outline-none placeholder:text-zinc-400 focus:border-primary-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          />
        </div>
        {query && (
          <span className="rt-micro shrink-0">
            匹配 {sorted.length} / {section.rows.length} 只
          </span>
        )}
      </div>

      <ReportTable
        cols={[
          { key: 'name', label: '标的' },
          { key: 'pct', label: '当日涨跌幅', align: 'right', numeric: true, onSort: () => handleSort('pct'), sortDir: sortDirOf('pct') },
          { key: 'close', label: '收盘价', align: 'right', numeric: true, onSort: () => handleSort('close'), sortDir: sortDirOf('close') },
          { key: 'week', label: '本周涨幅', align: 'right', numeric: true, onSort: () => handleSort('week_pct'), sortDir: sortDirOf('week_pct') },
          { key: 'turnover', label: '换手率', align: 'right', numeric: true, onSort: () => handleSort('turnover'), sortDir: sortDirOf('turnover') },
          { key: 'cap', label: '总市值(亿)', align: 'right', numeric: true, onSort: () => handleSort('market_cap'), sortDir: sortDirOf('market_cap') },
        ]}
        rows={sorted.map((r) => ({
          name: (
            <span className="font-medium text-zinc-800 dark:text-zinc-100">
              {r.name}
              <span className="ml-1.5 font-normal text-zinc-400 dark:text-zinc-500">{r.ts_code}</span>
            </span>
          ),
          pct: <PctCell text={r.pct_text} n={r.pct} />,
          close: <span>{r.close_text?.replace('元', '') ?? '—'}</span>,
          week: <PctCell text={r.week_pct_text} n={r.week_pct} />,
          turnover: <span className={turnoverCls(r.turnover)}>{r.turnover != null ? `${r.turnover}%` : '—'}</span>,
          cap: <span className={capCls(r.market_cap)}>{r.market_cap != null ? r.market_cap.toLocaleString('zh-CN') : '—'}</span>,
        }))}
        // 26rem 而非 60vh：该栏实际高度是 100dvh 减去 TopBar/头部条/KPI 行，
        // 与视口高度不是一回事，短视口下用 vh 会让 sticky thead 卡住
        maxHeight="max-h-[26rem]"
        empty={query ? '无匹配标的' : section.status === 'na' ? '标的池行情未接入' : '本期无标的池行情数据'}
        footer={footer}
        onRowClick={(i) => {
          const r = sorted[i]
          if (r) openEastmoneyNotices(r.ts_code)
        }}
      />
    </PanelShell>
  )
}
