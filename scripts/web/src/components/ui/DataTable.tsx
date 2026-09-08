import { useMemo } from 'react'
import { cn } from '@/lib/cn'
import { formatCell, isNumericColumn, MAX_ROWS, type DataTableModel } from '@/lib/table'

/**
 * 工具返回数据的只读表格：表头吸顶、数值列右对齐带千分位、斑马纹。
 * 列名原样透传（后端给什么显示什么），不做翻译、不补算任何数据。
 */
export function DataTable({
  model,
  className,
  maxHeight = 'max-h-[52vh]',
}: {
  model: DataTableModel
  className?: string
  maxHeight?: string
}) {
  // 数值列判定要扫全表，逐帧重算会拖慢流式渲染（store 每个 SSE 帧都重建 turns）
  const numericCols = useMemo(
    () => model.columns.map((_, i) => isNumericColumn(model.rows, i)),
    [model],
  )

  return (
    <div className={cn('space-y-1.5', className)}>
      <div
        className={cn(
          'overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800',
          maxHeight,
        )}
      >
        <table className="w-full border-collapse text-[12.5px]">
          <thead>
            <tr>
              {model.columns.map((c, i) => (
                <th
                  key={i}
                  scope="col"
                  className={cn(
                    'sticky top-0 z-10 whitespace-nowrap border-b border-zinc-200 bg-zinc-100 px-3 py-2 font-semibold text-zinc-600',
                    'dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200',
                    numericCols[i] ? 'text-right' : 'text-left',
                  )}
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {model.rows.map((row, r) => (
              <tr
                key={r}
                className="even:bg-zinc-50/70 hover:bg-primary-50/50 dark:even:bg-zinc-900/40 dark:hover:bg-primary-500/10"
              >
                {row.map((cell, c) => (
                  <td
                    key={c}
                    className={cn(
                      'whitespace-nowrap border-b border-zinc-100 px-3 py-1.5 text-zinc-700',
                      'dark:border-zinc-800/70 dark:text-zinc-300',
                      numericCols[c]
                        ? 'text-right font-mono tabular-nums'
                        : 'text-left',
                      cell === null && 'text-zinc-300 dark:text-zinc-600',
                    )}
                  >
                    {formatCell(cell, numericCols[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {model.truncated && (
        <p className="text-[11px] text-zinc-400 dark:text-zinc-500">
          共 {model.totalRows} 行，仅显示前 {MAX_ROWS} 行
        </p>
      )}
    </div>
  )
}
