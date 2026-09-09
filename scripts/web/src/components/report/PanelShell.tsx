import type { ReactNode } from 'react'
import type { Icon } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import type { SectionStatus } from '@/lib/quickReport'

/**
 * 快报仪表盘面板的卡片脚手架（替代旧 SectionShell，密度更高、挂 rt-card）。
 *
 * 约定：新面板一律用 `rounded-card` / `rounded-control`（.report-dense 子树内重定义了这两个
 * CSS 变量把圆角收成参考稿的 7px），**禁止 `rounded-xl` / `rounded-lg`**——那两个读的是
 * `--radius-xl` / `--radius-lg`，全局 @theme 变量，重定义会误伤对话/技能/设置页。
 * 字重只用 400 / 700（.report-dense :is(b,strong) 已固定为 700，正文默认 400）。
 */
type Props = {
  icon: Icon
  title: string
  status: SectionStatus
  note?: string | null
  /** 放在状态 Badge 旁边的附加信息（如「触发条件：同比 >+50%」） */
  hint?: ReactNode
  /** 表格类面板去掉内边距，让 ReportTable 自己控制（配合 rt-card-flush） */
  flush?: boolean
  children: ReactNode
}

export function PanelShell({ icon: Icon, title, status, note, hint, flush, children }: Props) {
  return (
    <section className={`rt-card ${flush ? 'rt-card-flush' : ''}`}>
      <div className={`mb-2.5 flex items-center gap-2 ${flush ? 'px-3 pt-3' : ''}`}>
        <Icon size={14} className="shrink-0 text-zinc-400 dark:text-zinc-500" weight="duotone" />
        <h2 className="rt-label">{title}</h2>
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
      <div className={flush ? 'px-3 pb-3' : ''}>{children}</div>
    </section>
  )
}
