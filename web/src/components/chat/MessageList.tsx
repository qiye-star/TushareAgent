import { Sparkle } from '@phosphor-icons/react'
import { useAutoScroll } from '@/hooks/useAutoScroll'
import { useChatStore } from '@/state/useChatStore'
import { MessageBubble } from './MessageBubble'

const SUGGESTIONS = [
  '比亚迪最近一季度的营收和净利润是多少？',
  '对比比亚迪和宁德时代的毛利率变化。',
  '从2024年报看比亚迪研发投入是多少？',
]

function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
        <Sparkle size={26} weight="fill" />
      </div>
      <h2 className="text-lg font-semibold tracking-tight text-zinc-800 dark:text-zinc-100">
        开始一次金融数据对话
      </h2>
      <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
        用自然语言提问，智能体会自行决定调用行情、财务或财报检索工具，再总结成中文回答。
      </p>
      <div className="mt-8 grid w-full max-w-md gap-2.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => window.dispatchEvent(new CustomEvent('suggest', { detail: s }))}
            className="rounded-xl border border-zinc-200 bg-white px-4 py-3 text-left text-[13.5px] text-zinc-600 transition-colors hover:border-primary-300 hover:bg-primary-50/40 hover:text-zinc-800 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-primary-500/40 dark:hover:bg-primary-500/5"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

export function MessageList({
  onRegenerate,
}: {
  onRegenerate?: (query: string) => void
}) {
  const turns = useChatStore((s) => s.turns)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const last = turns[turns.length - 1]
  const scrollDep = last ? last.answer.length + last.thinking.length : 0
  const ref = useAutoScroll<HTMLDivElement>(scrollDep)

  return (
    <div
      ref={ref}
      className="flex-1 overflow-y-auto px-4 py-6 md:px-8"
    >
      <div className="mx-auto flex min-h-full max-w-3xl flex-col">
        {turns.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="space-y-6">
            {turns.map((t, i) => (
              <MessageBubble
                key={t.id}
                turn={t}
                canRegenerate={i === turns.length - 1 && !isStreaming}
                onRegenerate={onRegenerate}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
