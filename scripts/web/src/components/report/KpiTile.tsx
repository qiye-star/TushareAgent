import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Props = {
  label: string
  value: ReactNode
  valueClassName?: string
  footnote?: ReactNode
}

/** 一块 KPI 指标：rt-label + rt-metric 数值 + rt-micro 脚注。宽度由父级（横向滚动/网格）控制。 */
export function KpiTile({ label, value, valueClassName, footnote }: Props) {
  return (
    <div className="rt-card min-w-[8.75rem] shrink-0 md:min-w-0">
      <div className="rt-label">{label}</div>
      <div className={cn('rt-metric mt-1', valueClassName)}>{value}</div>
      {footnote && <div className="rt-micro mt-0.5 truncate">{footnote}</div>}
    </div>
  )
}
