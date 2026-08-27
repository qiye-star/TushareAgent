import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/cn'
import { cleanAnswer } from '@/lib/format'
import type { CitationCard } from '@/lib/types'

/** Turn raw answer text's numeric citation markers `[n]` into internal links. */
function inlineRefs(answer: string): string {
  return answer.replace(/\[(\d+)\]/g, (_, n) => `[${n}](#/cit-${n})`)
}

type Props = {
  answer: string
  citations: CitationCard[]
  streaming?: boolean
  onJumpToRef?: (index: number) => void
  className?: string
}

export function AnswerBlock({
  answer,
  citations,
  streaming,
  onJumpToRef,
  className,
}: Props) {
  const text = inlineRefs(cleanAnswer(answer))

  return (
    <div className={cn('text-[15px] leading-relaxed text-zinc-800 dark:text-zinc-200', className)}>
      {text ? (
        <div className="prose prose-sm prose-zinc dark:prose-invert max-w-none">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkBreaks]}
          components={{
            // 表格卡片式容器（圆角边框 + 横向滚动）；行列样式见 globals.css 的 .answer-table
            table: ({ children }) => (
              <div className="my-4 overflow-x-auto">
                <table className="answer-table w-full text-left text-[13px]">{children}</table>
              </div>
            ),
            a: ({ href, children }) => {
              const match = typeof href === 'string' ? href.match(/^#\/cit-(\d+)/) : null
              if (match) {
                const index = Number(match[1])
                return (
                  <button
                    type="button"
                    onClick={() => onJumpToRef?.(index)}
                    className="inline-flex items-center gap-0.5 align-super text-[11px] font-medium leading-none text-primary-600 transition-colors hover:text-primary-500 dark:text-primary-400"
                  >
                    {children}
                  </button>
                )
              }
              return (
                <a
                  href={href as string}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary-600 underline decoration-primary-300 underline-offset-2 hover:text-primary-500 dark:text-primary-400"
                >
                  {children}
                </a>
              )
            },
          }}
        >
          {text}
        </ReactMarkdown>
        </div>
      ) : (
        <span className="text-zinc-400 dark:text-zinc-500">正在生成回答…</span>
      )}

      {streaming && (
        <span className="ml-1 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-primary-500 align-text-bottom" />
      )}
    </div>
  )
}
