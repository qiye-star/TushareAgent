import { ArrowSquareOut, Newspaper } from '@phosphor-icons/react'
import type { NewsSection } from '@/lib/quickReport'
import { PanelShell } from '../PanelShell'

/** 产业链催化事件（右栏 3）。`source.label` 显示当前是四级链里哪一层供的数。 */
export function PanelNews({ section }: { section: NewsSection }) {
  return (
    <PanelShell
      icon={Newspaper}
      title="催化事件"
      status={section.status}
      note={section.note}
      hint={section.source?.label ? <span className="rt-micro">{section.source.label}</span> : undefined}
    >
      {/* note 在这里是有信息量的：例如「无产业链关键词命中，展示市场头条」——
          说明这批条目没经过相关性过滤，读者该按「全市场头条」来看，而不是当成算力催化 */}
      {section.note && section.status !== 'na' && (
        <p className="rt-micro mb-2 rounded-control bg-amber-50/70 px-2 py-1 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400">
          {section.note}
        </p>
      )}
      {section.items.length === 0 ? (
        <p className="rt-micro">{section.status === 'na' ? '新闻接口未接入' : '本期无明显催化'}</p>
      ) : (
        <ul className="space-y-1.5">
          {section.items.map((r, i) => (
            <li key={i} className="flex items-start gap-2">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary-400 dark:bg-primary-500" />
              <div className="min-w-0">
                <p className="rt-body leading-relaxed text-zinc-700 dark:text-zinc-200">
                  {r.url ? (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-start gap-1 hover:text-primary-600 hover:underline dark:hover:text-primary-400"
                    >
                      {r.title}
                      <ArrowSquareOut size={10} className="mt-0.5 shrink-0 text-zinc-400" />
                    </a>
                  ) : (
                    r.title
                  )}
                </p>
                {(r.src || r.datetime) && (
                  <p className="rt-micro mt-0.5">{[r.src, r.datetime].filter(Boolean).join(' · ')}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </PanelShell>
  )
}
