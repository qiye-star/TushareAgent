import { cn } from '@/lib/cn'

type FunnelData = {
  recall_raw?: number
  fused?: number
  sections?: number
  pool?: number
  reranked?: number
  final?: number
  rerank_degraded?: boolean
}

const STAGES: { key: keyof FunnelData; label: string }[] = [
  { key: 'recall_raw', label: '候选' },
  { key: 'fused', label: 'RRF 融合' },
  { key: 'sections', label: '节' },
  { key: 'pool', label: '重排池' },
  { key: 'reranked', label: '重排' },
  { key: 'final', label: '完成' },
]

/** RAG 检索→重排漏斗可视化：候选 → 融合 → 节 → 重排池 → 重排 → 完成（数字级联渐显）。 */
export function RetrievalFunnel({ data }: { data: FunnelData }) {
  return (
    <div className="px-3 pb-2">
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1 pt-0.5">
        {STAGES.map((s, i) => {
          const value = data[s.key]
          const last = i === STAGES.length - 1
          return (
            <div key={s.key} className="flex shrink-0 items-center gap-1.5">
              <div
                className={cn(
                  'funnel-stage inline-flex items-center gap-1 rounded-md border border-zinc-200 bg-zinc-50 px-1.5 py-0.5',
                  'text-[11px] leading-none dark:border-zinc-700 dark:bg-zinc-900',
                )}
                style={{ animationDelay: `${i * 80}ms` }}
              >
                <span className="text-zinc-500 dark:text-zinc-400">{s.label}</span>
                <span
                  className={cn(
                    'font-semibold tabular-nums',
                    last ? 'text-primary-600 dark:text-primary-400' : 'text-zinc-700 dark:text-zinc-200',
                  )}
                >
                  {value ?? '–'}
                </span>
              </div>
              {!last && <span className="text-zinc-300 dark:text-zinc-600">›</span>}
            </div>
          )
        })}
      </div>
      {data.rerank_degraded && (
        <div className="mt-1.5 inline-flex items-center gap-1 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
          重排降级 · 已回退 RRF 序
        </div>
      )}
    </div>
  )
}
