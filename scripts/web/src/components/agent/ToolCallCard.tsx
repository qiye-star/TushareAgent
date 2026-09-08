import { useMemo, useState } from 'react'
import { CaretRight, ChartBar, CircleNotch } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'
import { prettyJson } from '@/lib/format'
import { parseToolTable } from '@/lib/table'
import { Badge } from '@/components/ui/Badge'
import { DataFallback } from '@/components/agent/DataFallback'
import { DataTable } from '@/components/ui/DataTable'

type Props = {
  name: string
  input: Record<string, unknown>
  result: { name: string; content: string; ok: boolean } | null
}

export function ToolCallCard({ name, input, result }: Props) {
  const [showInput, setShowInput] = useState(false)
  const [showOutput, setShowOutput] = useState(false)
  const [showRaw, setShowRaw] = useState(false)
  // 与引用来源卡共用同一个解析器 + 同一套表格渲染，保证同一份数据两处呈现一致
  const table = useMemo(
    () => (result?.ok ? parseToolTable(result.content) : null),
    [result?.ok, result?.content],
  )

  return (
    <div className="overflow-hidden rounded-xl border border-zinc-200 bg-zinc-50/60 dark:border-zinc-800 dark:bg-zinc-900/40">
      <div className="flex items-center gap-2 border-b border-zinc-200/70 px-3 py-2 dark:border-zinc-800">
        <ChartBar size={16} weight="fill" className="shrink-0 text-primary-500" />
        {/* 标题用人类语义，原始接口名收进「返回结果」的 meta 行 */}
        <span className="text-sm font-medium text-zinc-700 dark:text-zinc-200">数据</span>
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
              <div className="space-y-2 px-3 pb-2.5">
                <div className="flex flex-wrap items-center gap-x-2 text-[11.5px] text-zinc-400 dark:text-zinc-500">
                  <span>接口 {name}</span>
                  {table && (
                    <span>
                      · {table.totalRows} 行 × {table.columns.length} 列
                    </span>
                  )}
                </div>
                {table ? (
                  <>
                    <DataTable model={table} maxHeight="max-h-72" />
                    <button
                      type="button"
                      onClick={() => setShowRaw((v) => !v)}
                      className="flex items-center gap-1 text-[11.5px] text-zinc-400 transition-colors hover:text-zinc-600 dark:hover:text-zinc-300"
                    >
                      <CaretRight
                        size={11}
                        className={cn('transition-transform', showRaw && 'rotate-90')}
                      />
                      原始 JSON
                    </button>
                    {showRaw && (
                      <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-100/70 p-2.5 font-mono text-[11px] leading-relaxed text-zinc-500 dark:bg-zinc-900/60 dark:text-zinc-400">
                        {result.content}
                      </pre>
                    )}
                  </>
                ) : (
                  // 非表格返回（权限提示 / 公告正文 / 解析失败 / 失败态）→ 共享漂亮兜底
                  <DataFallback
                    text={result.content}
                    maxHeight="max-h-48"
                    tone={result.ok ? 'default' : 'error'}
                  />
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
