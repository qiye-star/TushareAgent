import { useState } from 'react'
import { Article, CaretDown, Crosshair } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'
import { citeSubtitle, prettyJson } from '@/lib/format'
import type { SourceCard as SourceCardType } from '@/lib/types'

function SourceCard({
  source,
  index,
  active,
}: {
  source: SourceCardType
  index: number
  active?: boolean
}) {
  const [open, setOpen] = useState(false)
  const subtitle = citeSubtitle(source)
  const isTool = source.type === 'tool'

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
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/40"
      >
        <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary-100 text-[11px] font-semibold text-primary-700 dark:bg-primary-500/20 dark:text-primary-300">
          {index + 1}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            {isTool ? (
              <Crosshair size={13} className="shrink-0 text-primary-500" />
            ) : (
              <Article size={13} className="shrink-0 text-primary-500" />
            )}
            <span className="truncate text-[13px] font-medium text-zinc-700 dark:text-zinc-200">
              {source.title}
            </span>
          </span>
          {subtitle && (
            <span className="mt-0.5 block truncate text-[12px] text-zinc-400 dark:text-zinc-500">
              {subtitle}
            </span>
          )}
        </span>
        <CaretDown
          size={14}
          className={cn('mt-1 shrink-0 text-zinc-400 transition-transform', open && 'rotate-180')}
        />
      </button>
      {open && (
        <div className="border-t border-zinc-200/70 px-3 py-2 dark:border-zinc-800">
          {isTool ? (
            <>
              {source.data && (
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11.5px] leading-relaxed text-zinc-600 dark:text-zinc-300">
                  {source.data}
                </pre>
              )}
            </>
          ) : (
            <p className="line-clamp-3 text-[12.5px] leading-relaxed text-zinc-500 dark:text-zinc-400">
              {source.excerpt || source.inline || '无原文片段'}
            </p>
          )}
          {isTool && source.params && (
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-zinc-400 dark:text-zinc-500">
              {prettyJson(source.params)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

export function CitationList({
  sources,
  activeIndex,
  hideHeader,
}: {
  sources: SourceCardType[]
  activeIndex?: number | null
  /** 在标题已由外层（如抽屉）提供时隐藏自身的「引用来源」小节头。 */
  hideHeader?: boolean
}) {
  if (!sources.length) return null

  return (
    <div className={cn('space-y-2', !hideHeader && 'mt-3')}>
      {!hideHeader && (
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
          <Crosshair size={12} />
          引用来源
        </div>
      )}
      <div className="space-y-2">
        {sources.map((s, i) => (
          <SourceCard key={i} source={s} index={i} active={activeIndex === i + 1} />
        ))}
      </div>
    </div>
  )
}
