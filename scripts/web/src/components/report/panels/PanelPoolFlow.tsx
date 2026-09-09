import { lazy, Suspense, useMemo } from 'react'
import { CurrencyCircleDollar } from '@phosphor-icons/react'
import { Skeleton } from '@/components/ui/Skeleton'
import { useChartTokens } from '@/hooks/useChartTokens'
import { pctClass } from '@/lib/format'
import type { BoardSection } from '@/lib/quickReport'
import { poolFlowOption } from '../charts/poolFlowOption'
import { PanelShell } from '../PanelShell'

const EChart = lazy(() => import('@/components/charts/EChart'))

/**
 * 资金流（左主区 2）：池级净流入合计 + 个股 TOP5 横向条形图。
 *
 * 从旧 SectionBoard 里拆出来——那个面板把「指数行情」和「个股资金流」两套无关数据
 * 塞在同一个标题下，`hasRowInflow` 的分叉就是这种混用的产物（生产环境恒 false，是死代码）。
 */
export function PanelPoolFlow({ section }: { section: BoardSection }) {
  const t = useChartTokens()
  const items = section.top_inflow
  const option = useMemo(() => poolFlowOption(items, t), [items, t])
  const pool = section.pool_inflow

  return (
    <PanelShell
      icon={CurrencyCircleDollar}
      title="标的池资金流"
      status={section.status}
      note={section.note}
      hint={pool?.text ? <span className="rt-micro">单位：亿元</span> : undefined}
    >
      {pool?.text && (
        <div className="mb-2.5 flex items-baseline gap-2">
          <span className="rt-micro">全池净流入合计</span>
          <span className={`rt-display rt-num ${pctClass(pool.value)}`}>{pool.text}</span>
        </div>
      )}

      {items.length > 0 ? (
        <Suspense fallback={<Skeleton className="h-[168px] w-full rounded-control" />}>
          <EChart
            option={option}
            className="h-[168px]"
            ariaLabel={`标的池资金净流入 TOP${items.length} 条形图：${items
              .map((i) => `${i.name} ${i.inflow_text ?? '—'} 亿`)
              .join('，')}`}
          />
        </Suspense>
      ) : (
        <p className="rt-micro">{section.status === 'na' ? '资金流未接入' : '本期无资金流数据'}</p>
      )}
    </PanelShell>
  )
}
