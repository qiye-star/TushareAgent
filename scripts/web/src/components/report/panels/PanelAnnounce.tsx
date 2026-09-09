import { ArrowSquareOut, Megaphone } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import type { AnnounceSection } from '@/lib/quickReport'
import { ANN_TONES } from '../cells'
import { PanelShell } from '../PanelShell'
import { ReportTable } from '../ReportTable'

/** 关键公告（左主区 4）。 */
export function PanelAnnounce({ section }: { section: AnnounceSection }) {
  return (
    <PanelShell icon={Megaphone} title="关键公告" status={section.status} note={section.note}>
      <ReportTable
        cols={[
          { key: 'name', label: '标的' },
          { key: 'type', label: '类型' },
          { key: 'title', label: '核心内容' },
          { key: 'date', label: '发布日期' },
        ]}
        rows={section.items.map((r) => ({
          name: r.ts_code ? (
            <a
              href={`https://data.eastmoney.com/notices/stock/${r.ts_code.split('.')[0]}.html`}
              target="_blank"
              rel="noreferrer"
              title="跳转该股票公告列表页（东方财富，无法直达单条公告原文）"
              className="inline-flex items-center gap-1 font-medium text-zinc-800 hover:text-primary-600 hover:underline dark:text-zinc-100 dark:hover:text-primary-400"
            >
              {r.name}
              <ArrowSquareOut size={11} className="shrink-0 text-zinc-400" />
            </a>
          ) : (
            <span className="font-medium text-zinc-800 dark:text-zinc-100">{r.name}</span>
          ),
          type: <Badge tone={ANN_TONES[r.type] ?? 'neutral'}>{r.type}</Badge>,
          title: (
            <span title={r.title} className="block max-w-[26rem] truncate text-zinc-600 dark:text-zinc-300">
              {r.title}
            </span>
          ),
          date: <span className="text-zinc-400 dark:text-zinc-500">{r.ann_date || '—'}</span>,
        }))}
        maxHeight="max-h-[22rem]"
        empty={section.status === 'na' ? '公告接口未接入' : '本期无关键公告'}
      />
    </PanelShell>
  )
}
