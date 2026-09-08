import { useMemo, useState, type ReactNode } from 'react'
import {
  ArrowDown,
  ArrowSquareOut,
  ArrowUp,
  Lightning,
  MagnifyingGlass,
  Megaphone,
  Newspaper,
  TrendUp,
  WarningCircle,
} from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/cn'
import { pctClass } from '@/lib/format'
import type {
  AnnounceSection as AnnounceSectionT,
  BoardSection as BoardSectionT,
  BriefSection,
  ForecastSection as ForecastSectionT,
  NewsSection as NewsSectionT,
  WatchlistSection as WatchlistSectionT,
} from '@/lib/quickReport'
import { ReportTable } from './ReportTable'
import { SectionShell } from './SectionShell'

/** 涨跌幅单元格（带符号 + 红涨绿跌）。 */
function PctCell({ text, n }: { text: string | null; n: number | null }) {
  return <span className={cnFont(pctClass(n))}>{text ?? '—'}</span>
}

function cnFont(cls: string) {
  return `font-medium ${cls}`
}

// ---------------------------------------------------------------------------
// ① 板块概览
// ---------------------------------------------------------------------------

const MEDAL_CLS: Record<number, string> = {
  1: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300',
  2: 'bg-zinc-200 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300',
  3: 'bg-orange-100 text-orange-700 dark:bg-orange-500/20 dark:text-orange-300',
}

export function SectionBoard({ section }: { section: BoardSectionT }) {
  // 指数来源（当前实际路径）没有板块级资金流概念——只在真有逐行数据（概念/板块环启用后）才展示 4 列表格，
  // 否则精简为 2 列 + 标的池整体资金流汇总行（复用已算好的个股资金流，不逐指数编造数字）。
  const hasRowInflow = section.rows.some((r) => r.inflow != null || r.remark)
  const maxTop = Math.max(1, ...section.top_inflow.map((r) => r.inflow ?? 0))

  return (
    <SectionShell icon={TrendUp} title="板块概览" status={section.status} note={section.note}>
      {!hasRowInflow && section.pool_inflow?.text && (
        <div className="mb-3 flex items-center gap-2 rounded-lg bg-zinc-50 px-3 py-2 text-[12.5px] dark:bg-zinc-800/50">
          <span className="text-zinc-500 dark:text-zinc-400">标的池整体资金流</span>
          <span className={cnFont(pctClass(section.pool_inflow.value))}>{section.pool_inflow.text}</span>
        </div>
      )}
      <ReportTable
        cols={
          hasRowInflow
            ? [
                { key: 'name', label: '板块 / 概念' },
                { key: 'pct', label: '当日涨跌幅', align: 'right', numeric: true },
                { key: 'inflow', label: '主力资金净流入(亿元)', align: 'right', numeric: true },
                { key: 'remark', label: '备注' },
              ]
            : [
                { key: 'name', label: '板块 / 概念' },
                { key: 'pct', label: '当日涨跌幅', align: 'right', numeric: true },
              ]
        }
        rows={section.rows.map((r) => ({
          name: (
            <span className="font-medium text-zinc-800 dark:text-zinc-100">
              {r.name}
              {r.provider && (
                <span className="ml-1.5 text-[10px] font-normal text-zinc-400 dark:text-zinc-500">
                  {r.provider}
                </span>
              )}
            </span>
          ),
          pct: <PctCell text={r.pct_text} n={r.pct} />,
          ...(hasRowInflow
            ? {
                inflow: <span className="text-zinc-700 dark:text-zinc-200">{r.inflow_text ?? '—'}</span>,
                remark: <span className="text-zinc-500 dark:text-zinc-400">{r.remark || ''}</span>,
              }
            : {}),
        }))}
        empty={section.status === 'na' ? '数据未接入' : '本期无板块行情数据'}
      />
      {section.top_inflow.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-[11.5px] font-semibold text-zinc-500 dark:text-zinc-400">
            本周资金净流入 TOP5
          </h3>
          <ReportTable
            cols={[
              { key: 'rank', label: '排名', align: 'right' },
              { key: 'name', label: '标的' },
              { key: 'inflow', label: '净流入金额(亿元)', align: 'right', numeric: true },
            ]}
            rows={section.top_inflow.map((r) => ({
              rank: (
                <span
                  className={cn(
                    'inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold',
                    MEDAL_CLS[r.rank] ?? 'text-zinc-400 dark:text-zinc-500',
                  )}
                >
                  {r.rank}
                </span>
              ),
              name: <span className="font-medium text-zinc-800 dark:text-zinc-100">{r.name}</span>,
              inflow: (
                <div className="relative flex h-5 items-center justify-end overflow-hidden rounded">
                  <div
                    className="absolute inset-y-0 right-0 bg-rose-100 dark:bg-rose-500/15"
                    style={{ width: `${Math.max(6, ((r.inflow ?? 0) / maxTop) * 100)}%` }}
                  />
                  <span className="relative pr-1.5 text-zinc-700 dark:text-zinc-200">{r.inflow_text ?? '—'}</span>
                </div>
              ),
            }))}
          />
        </div>
      )}
    </SectionShell>
  )
}

// ---------------------------------------------------------------------------
// ② 标的池行情速览
// ---------------------------------------------------------------------------

type WatchSortKey = 'pct' | 'close' | 'week_pct' | 'turnover' | 'market_cap' | null

/** 换手率分层强调色：<3% 淡出 / 10~20% 琥珀 / >20% 红（呼应第④段的警示语汇）。 */
function turnoverCls(t: number | null): string {
  if (t == null) return 'text-zinc-700 dark:text-zinc-200'
  if (t > 20) return 'font-semibold text-rose-600 dark:text-rose-400'
  if (t > 10) return 'font-medium text-amber-600 dark:text-amber-400'
  if (t < 3) return 'text-zinc-400 dark:text-zinc-500'
  return 'text-zinc-700 dark:text-zinc-200'
}

/** 市值分层：≥500 亿（大盘股）加粗强调，其余默认。 */
function capCls(cap: number | null): string {
  return cap != null && cap >= 500 ? 'font-semibold text-zinc-800 dark:text-zinc-100' : 'text-zinc-700 dark:text-zinc-200'
}

function openEastmoneyNotices(tsCode: string) {
  const code6 = tsCode.split('.')[0]
  if (!code6) return
  window.open(`https://data.eastmoney.com/notices/stock/${code6}.html`, '_blank', 'noopener,noreferrer')
}

export function SectionWatchlist({ section }: { section: WatchlistSectionT }) {
  const [query, setQuery] = useState('')
  const [sortKey, setSortKey] = useState<WatchSortKey>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

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

  // 首次点选某列默认降序（金融表惯例，大者优先）；再点切升序；第三次清除回池序。
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

  return (
    <SectionShell icon={Lightning} title="标的池行情速览" status={section.status} note={section.note}>
      <div className="mb-2.5 flex items-center gap-2">
        <div className="relative max-w-[220px] flex-1">
          <MagnifyingGlass
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400 dark:text-zinc-500"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索标的名称 / 代码"
            className="w-full rounded-lg border border-zinc-300 bg-white py-1.5 pl-7 pr-2.5 text-[12.5px] text-zinc-800 outline-none placeholder:text-zinc-400 focus:border-primary-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          />
        </div>
        {query && (
          <span className="shrink-0 text-[11px] text-zinc-400 dark:text-zinc-500">
            匹配 {sorted.length} / {section.rows.length} 只
          </span>
        )}
      </div>
      <ReportTable
        cols={[
          { key: 'name', label: '标的' },
          {
            key: 'pct',
            label: '当日涨跌幅',
            align: 'right',
            numeric: true,
            onSort: () => handleSort('pct'),
            sortDir: sortDirOf('pct'),
          },
          {
            key: 'close',
            label: '收盘价',
            align: 'right',
            numeric: true,
            onSort: () => handleSort('close'),
            sortDir: sortDirOf('close'),
          },
          {
            key: 'week',
            label: '本周涨幅',
            align: 'right',
            numeric: true,
            onSort: () => handleSort('week_pct'),
            sortDir: sortDirOf('week_pct'),
          },
          {
            key: 'turnover',
            label: '换手率',
            align: 'right',
            numeric: true,
            onSort: () => handleSort('turnover'),
            sortDir: sortDirOf('turnover'),
          },
          {
            key: 'cap',
            label: '总市值(亿)',
            align: 'right',
            numeric: true,
            onSort: () => handleSort('market_cap'),
            sortDir: sortDirOf('market_cap'),
          },
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
        maxHeight="max-h-[60vh]"
        empty={query ? '无匹配标的' : section.status === 'na' ? '标的池行情未接入' : '本期无标的池行情数据'}
        footer={
          section.pool_count > 0
            ? `共 ${section.pool_count} 只，返回 ${section.row_count} 只${section.missing_codes > 0 ? `（缺失 ${section.missing_codes}）` : ''} · 点击行跳转个股公告页`
            : undefined
        }
        onRowClick={(i) => {
          const r = sorted[i]
          if (r) openEastmoneyNotices(r.ts_code)
        }}
      />
    </SectionShell>
  )
}

// ---------------------------------------------------------------------------
// ③ 关键公告
// ---------------------------------------------------------------------------

const ANN_TONES: Record<string, 'primary' | 'error' | 'success' | 'neutral'> = {
  业绩预告: 'primary',
  业绩快报: 'primary',
  股东增减持: 'error',
  回购: 'success',
  募投融资: 'neutral',
  重大合同: 'neutral',
  分红: 'neutral',
}

export function SectionAnnounce({ section }: { section: AnnounceSectionT }) {
  return (
    <SectionShell icon={Megaphone} title="关键公告" status={section.status} note={section.note}>
      <ReportTable
        cols={[
          { key: 'name', label: '标的' },
          { key: 'type', label: '类型' },
          { key: 'title', label: '核心内容' },
          { key: 'date', label: '发布日期' },
        ]}
        rows={section.items.map((r) => ({
          name: r.ts_code ? (
            <a
              href={`https://data.eastmoney.com/notices/stock/${r.ts_code.split('.')[0]}.html`}
              target="_blank"
              rel="noreferrer"
              title="跳转该股票公告列表页（东方财富，无法直达单条公告原文）"
              className="inline-flex items-center gap-1 font-medium text-zinc-800 hover:text-primary-600 hover:underline dark:text-zinc-100 dark:hover:text-primary-400"
            >
              {r.name}
              <ArrowSquareOut size={11} className="shrink-0 text-zinc-400" />
            </a>
          ) : (
            <span className="font-medium text-zinc-800 dark:text-zinc-100">{r.name}</span>
          ),
          type: <Badge tone={ANN_TONES[r.type] ?? 'neutral'}>{r.type}</Badge>,
          title: (
            <span title={r.title} className="block max-w-[28rem] truncate text-zinc-600 dark:text-zinc-300">
              {r.title}
            </span>
          ),
          date: <span className="text-zinc-400 dark:text-zinc-500">{r.ann_date || '—'}</span>,
        }))}
        empty={section.status === 'na' ? '公告接口未接入' : '本期无关键公告'}
      />
    </SectionShell>
  )
}

// ---------------------------------------------------------------------------
// ④ 业绩预告异动提示
// ---------------------------------------------------------------------------

export function SectionForecast({ section }: { section: ForecastSectionT }) {
  const hint = (
    <span className="text-[10.5px] text-zinc-400 dark:text-zinc-500">
      触发条件：同比 &gt; +{section.thresholds.up}% 或 &lt; {section.thresholds.down}%（可在 watchlist.json 配置）
    </span>
  )
  const items = section.items
  return (
    <SectionShell icon={WarningCircle} title="业绩预告异动提示" status={section.status} note={section.note} hint={hint}>
      {items.length === 0 ? (
        <div className="text-[12.5px] text-zinc-400 dark:text-zinc-500">
          {section.status === 'na' ? '业绩预告接口未接入' : '本期无异常'}
        </div>
      ) : (
        <ul className="space-y-2.5">
          {items.map((r, i) => (
            <li key={i} className="rounded-lg border border-zinc-100 bg-zinc-50/60 px-3 py-2.5 dark:border-zinc-800 dark:bg-zinc-800/30">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[13px] font-semibold text-zinc-800 dark:text-zinc-100">{r.name}</span>
                {r.scope && <span className="text-[11px] text-zinc-400 dark:text-zinc-500">{r.scope}</span>}
                <span className={cnFont('text-[12.5px] text-primary-600 dark:text-primary-400')}>{r.yoy_text}</span>
                {r.hits.includes('up') && (
                  <Badge tone="primary">
                    <ArrowUp size={11} weight="bold" /> 正向异动
                  </Badge>
                )}
                {r.hits.includes('down') && (
                  <Badge tone="error">
                    <ArrowDown size={11} weight="bold" /> 负向异动
                  </Badge>
                )}
              </div>
              {r.reason && (
                <p className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                  {r.reason}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </SectionShell>
  )
}

// ---------------------------------------------------------------------------
// ⑤ 产业链催化事件
// ---------------------------------------------------------------------------

export function SectionNews({ section }: { section: NewsSectionT }) {
  return (
    <SectionShell icon={Newspaper} title="产业链催化事件" status={section.status} note={section.note}>
      {section.items.length === 0 ? (
        <div className="text-[12.5px] text-zinc-400 dark:text-zinc-500">
          {section.status === 'na' ? '新闻接口未接入' : '本期无明显催化'}
        </div>
      ) : (
        <ul className="space-y-2">
          {section.items.map((r, i) => (
            <li key={i} className="flex items-start gap-2.5">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary-400 dark:bg-primary-500" />
              <div className="min-w-0">
                <p className="text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-200">
                  {r.url ? (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-start gap-1 hover:text-primary-600 hover:underline dark:hover:text-primary-400"
                    >
                      {r.title}
                      <ArrowSquareOut size={11} className="mt-0.5 shrink-0 text-zinc-400" />
                    </a>
                  ) : (
                    r.title
                  )}
                </p>
                {(r.src || r.datetime) && (
                  <p className="mt-0.5 text-[11px] text-zinc-400 dark:text-zinc-500">
                    {[r.src, r.datetime].filter(Boolean).join(' · ')}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </SectionShell>
  )
}

// ---------------------------------------------------------------------------
// ⑥ 一句话研判
// ---------------------------------------------------------------------------

export function SectionBrief({ brief }: { brief: BriefSection }) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-soft dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-center gap-2">
        <TrendUp size={15} className="shrink-0 text-zinc-400 dark:text-zinc-500" weight="duotone" />
        <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
          一句话研判
        </h2>
        <span className="ml-auto text-[10.5px] text-zinc-400 dark:text-zinc-500">
          {brief.chars} / 150 字
        </span>
      </div>
      <blockquote className="rounded-r-lg border-l-2 border-primary-500 bg-primary-50/60 px-4 py-3 dark:bg-primary-500/10">
        <p className="text-[13.5px] leading-relaxed text-zinc-700 dark:text-zinc-200">{brief.text}</p>
      </blockquote>
    </section>
  )
}
