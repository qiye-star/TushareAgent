import { cn } from '@/lib/cn'
import { prettyJson } from '@/lib/format'

type Props = {
  /** 工具返回的原文（structured.sources[].data 或 ToolResult.content）。 */
  text: string | null | undefined
  maxHeight?: string
  tone?: 'default' | 'error'
  className?: string
}

/**
 * 非表格返回的「漂亮兜底」：parseToolTable 不收的数据（空 data、{data:{items}} 信封、单业务对象）
 * 以及纯文本（权限提示 / 上游报错 / 截断残留）不再裸展示最小化 JSON，而是：
 * - 是 JSON → 缩进美化（prettyJson，indent 2）在滚动容器里展示；
 * - 不是 JSON → 当正文文本块（不套 monospace 裸 <pre>），若像断掉的 JSON（以 { / [ 开头但解析失败）
 *   追加一行弱提示「（返回内容可能不完整）」。
 * tone='error' 用于失败态（rose 色）。
 */
export function DataFallback({ text, maxHeight, tone = 'default', className }: Props) {
  const raw = (text ?? '').trim()
  if (!raw) {
    return (
      <div className={cn('text-[11.5px] italic text-zinc-400 dark:text-zinc-500', maxHeight, className)}>
        无返回内容
      </div>
    )
  }

  const textTone = tone === 'error' ? 'text-rose-600 dark:text-rose-400' : 'text-zinc-600 dark:text-zinc-300'

  let isJson = false
  try {
    JSON.parse(raw)
    isJson = true
  } catch {
    isJson = false
  }

  if (isJson) {
    return (
      <pre
        className={cn(
          'overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-50 p-2.5 font-mono text-[11.5px] leading-relaxed dark:bg-zinc-900/60',
          textTone,
          maxHeight,
          className,
        )}
      >
        {prettyJson(JSON.parse(raw) as unknown)}
      </pre>
    )
  }

  // 不是 JSON：按正文文本块呈现；若长得像断掉的 JSON，给一行弱提示
  const looksBrokenJson = raw.startsWith('{') || raw.startsWith('[')
  return (
    <div className={cn('whitespace-pre-wrap text-[11.5px] leading-relaxed', textTone, maxHeight, className)}>
      {looksBrokenJson ? `${raw}\n（返回内容可能不完整）` : raw}
    </div>
  )
}
