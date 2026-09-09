import { cn } from '@/lib/cn'
import { pctClass } from '@/lib/format'

/**
 * 表格单元格的小型格式化辅助——从旧 ReportSections.tsx 搬迁而来（该文件已被面板化重写取代）。
 * 保持逐字节行为不变，只是换了个家。
 */

/** 涨跌幅单元格（带符号 + 红涨绿跌）。 */
export function PctCell({ text, n }: { text: string | null; n: number | null }) {
  return <span className={`font-medium ${pctClass(n)}`}>{text ?? '—'}</span>
}

/** 换手率分层强调色：<3% 淡出 / 10~20% 琥珀 / >20% 红（呼应异动段的警示语汇）。 */
export function turnoverCls(t: number | null): string {
  if (t == null) return 'text-zinc-700 dark:text-zinc-200'
  if (t > 20) return 'font-semibold text-rose-600 dark:text-rose-400'
  if (t > 10) return 'font-medium text-amber-600 dark:text-amber-400'
  if (t < 3) return 'text-zinc-400 dark:text-zinc-500'
  return 'text-zinc-700 dark:text-zinc-200'
}

/** 市值分层：≥500 亿（大盘股）加粗强调，其余默认。 */
export function capCls(cap: number | null): string {
  return cap != null && cap >= 500 ? 'font-semibold text-zinc-800 dark:text-zinc-100' : 'text-zinc-700 dark:text-zinc-200'
}

export function openEastmoneyNotices(tsCode: string) {
  const code6 = tsCode.split('.')[0]
  if (!code6) return
  window.open(`https://data.eastmoney.com/notices/stock/${code6}.html`, '_blank', 'noopener,noreferrer')
}

export const ANN_TONES: Record<string, 'primary' | 'error' | 'success' | 'neutral'> = {
  业绩预告: 'primary',
  业绩快报: 'primary',
  股东增减持: 'error',
  回购: 'success',
  募投融资: 'neutral',
  重大合同: 'neutral',
  分红: 'neutral',
}

export const MEDAL_CLS: Record<number, string> = {
  1: 'bg-amber-100 text-amber-700 dark:bg-amber-500/20 dark:text-amber-300',
  2: 'bg-zinc-200 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300',
  3: 'bg-orange-100 text-orange-700 dark:bg-orange-500/20 dark:text-orange-300',
}

export function RankBadge({ rank }: { rank: number }) {
  return (
    <span
      className={cn(
        'inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold',
        MEDAL_CLS[rank] ?? 'text-zinc-400 dark:text-zinc-500',
      )}
    >
      {rank}
    </span>
  )
}
