import { useState } from 'react'
import { CaretRight, CircleNotch, Terminal } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'
import { prettyJson } from '@/lib/format'
import { Badge } from '@/components/ui/Badge'
import { Collapsible } from '@/components/ui/Collapsible'

type Props = {
  name: string
  input: Record<string, unknown>
  result: { name: string; content: string; ok: boolean } | null
}

export function ToolCallCard({ name, input, result }: Props) {
  const [showInput, setShowInput] = useState(false)
  const [showOutput, setShowOutput] = useState(false)

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50/60 dark:border-zinc-800 dark:bg-zinc-900/40">
      <div className="flex items-center gap-2 border-b border-zinc-200/70 px-3 py-2 dark:border-zinc-800">
        <Terminal size={16} weight="fill" className="shrink-0 text-primary-500" />
        <span className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
          {name}
        </span>
        {result ? (
          <Badge tone={result.ok ? 'success' : 'error'} className="ml-auto">
            {result.ok ? '成功' : '失败'}
          </Badge>
        ) : (
          <Badge tone="primary" className="ml-auto">
            <span className="inline-flex items-center gap-1.5">
              <CircleNotch size={12} className="animate-spin" />
              调用中
            </span>
          </Badge>
        )}
      </div>

      <div className="divide-y divide-zinc-200/70 dark:divide-zinc-800">
        <button
          type="button"
          onClick={() => setShowInput((v) => !v)}
          className="flex w-full items-center gap-1.5 px-3 py-2 text-xs text-zinc-500 transition-colors hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
        >
          <CaretRight
            size={12}
            className={cn('transition-transform', showInput && 'rotate-90')}
          />
          工具入参
        </button>
        {showInput && (
          <pre className="overflow-x-auto whitespace-pre-wrap px-3 pb-2 font-mono text-[11.5px] leading-relaxed text-zinc-600 dark:text-zinc-300">
            {prettyJson(input)}
          </pre>
        )}

        {result && (
          <>
            <button
              type="button"
              onClick={() => setShowOutput((v) => !v)}
              className="flex w-full items-center gap-1.5 px-3 py-2 text-xs text-zinc-500 transition-colors hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
            >
              <CaretRight
                size={12}
                className={cn('transition-transform', showOutput && 'rotate-90')}
              />
              返回结果
            </button>
            {showOutput && (
              <pre
                className={cn(
                  'max-h-48 overflow-auto whitespace-pre-wrap px-3 pb-2 font-mono text-[11.5px] leading-relaxed',
                  result.ok
                    ? 'text-zinc-600 dark:text-zinc-300'
                    : 'text-rose-600 dark:text-rose-400',
                )}
              >
                {result.content}
              </pre>
            )}
          </>
        )}
      </div>
    </div>
  )
}
