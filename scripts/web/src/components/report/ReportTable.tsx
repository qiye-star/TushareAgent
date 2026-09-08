import type { ReactNode } from 'react'
import { ArrowDown, ArrowUp } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'

/**
 * 快报专用表格：单元格支持 React 节点（涨跌着色 / Badge / 链接），这是 DataTable（纯文本单元格）
 * 覆盖不了的场景；视觉 token 复用 DataTable 的既有惯例（sticky 表头 / 数值列右对齐等宽 /
 * 斑马纹 / null→'—' / limit 截断提示）。
 */

export type ReportCol = {
  key: string
  label: string
  align?: 'left' | 'right'
  /** 数值列：右对齐 + font-mono tabular-nums（表头右侧额外留白适配滚动条） */
  numeric?: boolean
  className?: string
  /** 可排序列：点击表头触发；sortDir 为当前该列的排序方向（null = 未排序），由调用方持有状态 */
  onSort?: () => void
  sortDir?: 'asc' | 'desc' | null
}

export type ReportRow = Record<string, ReactNode>

type Props<T extends object = ReportRow> = {
  cols: ReportCol[]
  rows: T[]
  /** 渲染上限（超出显示截断脚注），后端已截断时前端不会触发；0/undefined = 不截断 */
  limit?: number
  totalCount?: number
  maxHeight?: string
  empty?: string | null
  footer?: ReactNode
  /** 行点击（如跳转个股页）；传入时行加 hover 高亮与指针光标 */
  onRowClick?: (index: number) => void
}

export function ReportTable<T extends object = ReportRow>({
  cols,
  rows,
  limit,
  totalCount,
  maxHeight,
  empty,
  footer,
  onRowClick,
}: Props<T>) {
  const visible = limit && limit > 0 ? rows.slice(0, limit) : rows
  const truncated = !!limit && limit > 0 && rows.length > limit

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-200 px-3 py-5 text-center text-[12.5px] text-zinc-400 dark:border-zinc-700 dark:text-zinc-500">
        {empty ?? '暂无数据'}
      </div>
    )
  }

  return (
    <div> {/* 容器：外框 + 内滚动（表格自身 overflow-x） */}
      <div className={cn('overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800', maxHeight)}>
        <table className="w-full border-collapse text-[12.75px]">
          <thead className="sticky top-0 z-10">
            <tr className="bg-zinc-100 dark:bg-zinc-800">
              {cols.map((c) => (
                <th
                  key={c.key}
                  onClick={c.onSort}
                  className={cn(
                    'whitespace-nowrap border-b-2 border-zinc-200 px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:border-zinc-700 dark:text-zinc-400',
                    c.align === 'right' && 'text-right',
                    c.numeric && 'pr-4', // 右对齐列与滚动条留白
                    c.onSort && 'cursor-pointer select-none hover:bg-zinc-200 dark:hover:bg-zinc-700',
                    c.className,
                  )}
                >
                  <span className={cn('inline-flex items-center gap-0.5', c.align === 'right' && 'flex-row-reverse')}>
                    {c.label}
                    {c.onSort && (
                      <span className="inline-flex w-3 shrink-0 justify-center text-zinc-400 dark:text-zinc-500">
                        {c.sortDir === 'asc' && <ArrowUp size={10} weight="bold" />}
                        {c.sortDir === 'desc' && <ArrowDown size={10} weight="bold" />}
                      </span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr
                key={i}
                onClick={onRowClick ? () => onRowClick(i) : undefined}
                className={cn(
                  'border-b border-zinc-100 last:border-0 hover:bg-primary-50/40 dark:border-zinc-800/60 dark:hover:bg-primary-500/5 even:bg-zinc-50/60 dark:even:bg-zinc-900/30',
                  onRowClick && 'cursor-pointer',
                )}
              >
                {cols.map((c) => {
                  const cell = (row as ReportRow)[c.key]
                  return (
                    <td
                      key={c.key}
                      className={cn(
                        'px-3 py-1.5 align-middle text-zinc-700 dark:text-zinc-200',
                        c.align === 'right' && 'text-right',
                        c.numeric && 'font-mono tabular-nums text-[12.5px] pr-4',
                        c.className,
                      )}
                    >
                      {cell === undefined || cell === null || cell === '' ? (
                        <span className="text-zinc-300 dark:text-zinc-600">—</span>
                      ) : (
                        cell
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(truncated || footer) && (
        <div className={cn('mt-1.5 px-1 text-[11px]', truncated ? 'text-amber-600 dark:text-amber-400' : 'text-zinc-400 dark:text-zinc-500')}>
          {truncated && `共 ${rows.length} 行，仅显示前 ${limit} 行`}
          {truncated && footer ? ' · ' : ''}
          {footer}
        </div>
      )}
    </div>
  )
}
