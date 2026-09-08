import type { ReactNode } from 'react'
import type { Icon } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import type { SectionStatus } from '@/lib/quickReport'

/**
 * 快报六段的卡片脚手架（项目内联卡片惯例，无 Card 组件）：
 * 小节头（uppercase 小字 + Phosphor 图标）+ 右侧状态 Badge + 内容区。
 * status：ok（正常渲染内容）/ empty（本期无数据）/ na（数据未接入，title 显示 reason）。
 */

type Props = {
  icon: Icon
  title: string
  status: SectionStatus
  note?: string | null
  /** 放在状态 Badge 旁边的附加信息（如「触发条件：同比 >+50%」） */
  hint?: ReactNode
  children: ReactNode
}

export function SectionShell({ icon: Icon, title, status, note, hint, children }: Props) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-soft dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-center gap-2">
        <Icon size={15} className="shrink-0 text-zinc-400 dark:text-zinc-500" weight="duotone" />
        <h2 className="text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
          {title}
        </h2>
        <div className="ml-auto flex items-center gap-2">
          {hint}
          {status === 'na' && (
            <span title={note ?? undefined}>
              <Badge tone="neutral">数据未接入</Badge>
            </span>
          )}
          {status === 'empty' && <Badge tone="primary">本期无数据</Badge>}
        </div>
      </div>
      {children}
    </section>
  )
}
