import { Quotes } from '@phosphor-icons/react'
import type { BriefSection } from '@/lib/quickReport'
import { PanelShell } from '../PanelShell'

/**
 * 一句话研判（右栏 1，置顶）：信息密度最高的一块，所以放在右栏最上面。
 * 改用 PanelShell —— 旧 SectionBrief 自己内联复制了一份卡片头，与 SectionShell 双份维护。
 */
export function PanelBrief({ brief }: { brief: BriefSection }) {
  return (
    <PanelShell
      icon={Quotes}
      title="一句话研判"
      status="ok"
      hint={<span className="rt-micro">{brief.chars} / 150 字</span>}
    >
      <blockquote className="border-l-2 border-primary-500 pl-2.5">
        <p className="rt-lede text-zinc-700 dark:text-zinc-200">{brief.text}</p>
      </blockquote>
    </PanelShell>
  )
}
