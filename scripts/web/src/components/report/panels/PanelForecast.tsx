import { ArrowDown, ArrowUp, WarningCircle } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import type { ForecastSection } from '@/lib/quickReport'
import { PanelShell } from '../PanelShell'

/** 业绩预告异动（右栏 2）：窄栏布局，卡片列表而非表格。 */
export function PanelForecast({ section }: { section: ForecastSection }) {
  return (
    <PanelShell
      icon={WarningCircle}
      title="业绩预告异动"
      status={section.status}
      note={section.note}
      hint={
        <span className="rt-micro">
          &gt;+{section.thresholds.up}% 或 &lt;{section.thresholds.down}%
        </span>
      }
    >
      {section.items.length === 0 ? (
        <p className="rt-micro">
          {section.status === 'na' ? '业绩预告接口未接入' : '本期无异常（披露淡季属正常）'}
        </p>
      ) : (
        <ul className="space-y-2">
          {section.items.map((r, i) => (
            <li key={i} className="rounded-control border border-[var(--rt-line-soft)] bg-white/50 px-2.5 py-2 dark:bg-zinc-800/30">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="rt-body font-bold text-zinc-800 dark:text-zinc-100">{r.name}</span>
                {r.scope && <span className="rt-micro">{r.scope}</span>}
                <span className="rt-num rt-body font-medium text-primary-600 dark:text-primary-400">{r.yoy_text}</span>
                {r.hits.includes('up') && (
                  <Badge tone="primary">
                    <ArrowUp size={10} weight="bold" /> 正向
                  </Badge>
                )}
                {r.hits.includes('down') && (
                  <Badge tone="error">
                    <ArrowDown size={10} weight="bold" /> 负向
                  </Badge>
                )}
              </div>
              {r.reason && <p className="rt-micro mt-1 line-clamp-3">{r.reason}</p>}
            </li>
          ))}
        </ul>
      )}
    </PanelShell>
  )
}
