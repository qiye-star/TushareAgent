import { useState } from 'react'
import { Article, ArrowSquareOut, CaretDown, CaretRight, ChartBar } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'
import { citeSubtitle, formatDate, prettyJson } from '@/lib/format'
import { parseToolTable } from '@/lib/table'
import type { SourceCard as SourceCardType } from '@/lib/types'
import { DataFallback } from '@/components/agent/DataFallback'
import { DataTable } from '@/components/ui/DataTable'
import { Dialog } from '@/components/ui/Dialog'

type ToolSource = Extract<SourceCardType, { type: 'tool' }>

/** 「数据」弹窗的副标题：接口名 + 行列数。 */
function metaLine(source: ToolSource, table: ReturnType<typeof parseToolTable>): string | undefined {
  const parts: string[] = []
  if (source.title) parts.push(`接口 ${source.title}`)
  if (table) parts.push(`${table.totalRows} 行 × ${table.columns.length} 列`)
  return parts.length ? parts.join(' · ') : undefined
}

/** 弹窗正文：能解析成表格 → 表格；否则原文兜底（权限提示 / 公告正文 / 截断解析失败）。 */
function ToolDataBody({ source }: { source: ToolSource }) {
  const [showRaw, setShowRaw] = useState(false)
  const table = parseToolTable(source.data)
  const tsCode = source.params?.ts_code

  return (
    <div className="space-y-2.5">
      {Boolean(tsCode) && (
        <div className="text-[11.5px] text-zinc-400 dark:text-zinc-500">· {String(tsCode)}</div>
      )}

      {table ? (
        <DataTable model={table} />
      ) : (
        <DataFallback text={source.data} maxHeight="max-h-[52vh]" />
      )}

      {(table || source.params) && (
        <>
          <button
            type="button"
            onClick={() => setShowRaw((v) => !v)}
            className="flex items-center gap-1 text-[11.5px] text-zinc-400 transition-colors hover:text-zinc-600 dark:hover:text-zinc-300"
          >
            <CaretRight size={11} className={cn('transition-transform', showRaw && 'rotate-90')} />
            原始返回
          </button>
          {showRaw && (
            <>
              {table && (
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-50 p-2.5 font-mono text-[11px] leading-relaxed text-zinc-500 dark:bg-zinc-900/60 dark:text-zinc-400">
                  {source.data}
                </pre>
              )}
              {source.params && (
                <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-zinc-400 dark:text-zinc-500">
                  {prettyJson(source.params)}
                </pre>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}

function SourceCard({
  source,
  index,
  active,
  query,
  createdAt,
  onOpenData,
}: {
  source: SourceCardType
  index: number
  active?: boolean
  /** 本轮用户原始问题，作为卡片副标题（比接口名更能说明这条来源在答什么）。 */
  query?: string
  createdAt?: string
  /** tool 类型卡片点击时触发：打开居中「数据」弹窗（rag 类型不用，走原地展开）。 */
  onOpenData: () => void
}) {
  const [open, setOpen] = useState(false)
  const isTool = source.type === 'tool'
  const kindLabel = isTool ? '数据' : '年报'
  const date = formatDate(createdAt)
  const ragSubtitle = isTool ? '' : citeSubtitle(source)

  return (
    <div
      id={`cit-${index + 1}`}
      className={cn(
        'overflow-hidden rounded-xl border',
        active
          ? 'border-primary-300 bg-primary-50/40 dark:border-primary-500/40 dark:bg-primary-500/5'
          : 'border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900/40',
        isTool && 'border-l-2 border-l-primary-400',
      )}
    >
      <button
        type="button"
        onClick={isTool ? onOpenData : () => setOpen((v) => !v)}
        className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/40"
      >
        <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary-100 text-[11px] font-semibold text-primary-700 dark:bg-primary-500/20 dark:text-primary-300">
          {index + 1}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            {isTool ? (
              <ChartBar size={13} weight="fill" className="shrink-0 text-primary-500" />
            ) : (
              <Article size={13} className="shrink-0 text-primary-500" />
            )}
            <span className="text-[13px] font-medium text-zinc-700 dark:text-zinc-200">
              {kindLabel}
            </span>
          </span>
          {query && (
            <span className="mt-1 block line-clamp-2 text-[12.5px] leading-relaxed text-zinc-600 dark:text-zinc-300">
              {query}
            </span>
          )}
          {date && (
            <span className="mt-0.5 block text-[11.5px] tabular-nums text-zinc-400 dark:text-zinc-500">
              {date}
            </span>
          )}
        </span>
        {isTool ? (
          <ArrowSquareOut size={14} className="mt-1 shrink-0 text-zinc-400" />
        ) : (
          <CaretDown
            size={14}
            className={cn('mt-1 shrink-0 text-zinc-400 transition-transform', open && 'rotate-180')}
          />
        )}
      </button>
      {!isTool && open && (
        <div className="border-t border-zinc-200/70 px-3 py-2.5 dark:border-zinc-800">
          <div className="space-y-1.5">
            {ragSubtitle && (
              <p className="text-[11.5px] text-zinc-400 dark:text-zinc-500">{ragSubtitle}</p>
            )}
            <p className="text-[12.5px] leading-relaxed text-zinc-500 dark:text-zinc-400">
              {source.excerpt || source.inline || '无原文片段'}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

export function CitationList({
  sources,
  activeIndex,
  hideHeader,
  query,
  createdAt,
}: {
  sources: SourceCardType[]
  activeIndex?: number | null
  /** 在标题已由外层（如抽屉）提供时隐藏自身的「引用来源」小节头。 */
  hideHeader?: boolean
  /** 本轮问题，用作每张来源卡的副标题。 */
  query?: string
  /** 本轮时间（ISO），用作每张来源卡的日期行。 */
  createdAt?: string
}) {
  // tool 类型来源卡点击后弹出的「数据」居中弹窗：记录当前打开的是第几条来源
  const [dataDialog, setDataDialog] = useState<number | null>(null)
  if (!sources.length) return null

  const activeSource = dataDialog != null ? (sources[dataDialog] as ToolSource) : null
  const activeTable = activeSource ? parseToolTable(activeSource.data) : null

  return (
    <div className={cn('space-y-2', !hideHeader && 'mt-3')}>
      {!hideHeader && (
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
          <ChartBar size={12} />
          引用来源
        </div>
      )}
      <div className="space-y-2">
        {sources.map((s, i) => (
          <SourceCard
            key={i}
            source={s}
            index={i}
            active={activeIndex === i + 1}
            query={query}
            createdAt={createdAt}
            onOpenData={() => setDataDialog(i)}
          />
        ))}
      </div>

      <Dialog
        open={dataDialog != null}
        onOpenChange={(o) => !o && setDataDialog(null)}
        size="wide"
        title="数据"
        description={activeSource ? metaLine(activeSource, activeTable) : undefined}
      >
        {activeSource && <ToolDataBody source={activeSource} />}
      </Dialog>
    </div>
  )
}
