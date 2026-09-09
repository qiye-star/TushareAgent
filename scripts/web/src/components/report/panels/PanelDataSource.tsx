import { useState } from 'react'
import { CaretDown, Database, Warning } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/cn'
import { formatRelative, formatTime } from '@/lib/format'
import type { McpSource } from '@/lib/mcp'
import type { QuickReport, SectionStatus } from '@/lib/quickReport'
import type { QuickReportStatus, SectionKey } from '@/lib/quickReportStatus'
import { PanelShell } from '../PanelShell'

const SECTION_LABELS: Record<SectionKey, string> = {
  board: '板块概览',
  watchlist: '标的池行情',
  announce: '关键公告',
  forecast: '业绩预告',
  news: '催化事件',
}

const SECTION_ORDER: SectionKey[] = ['board', 'watchlist', 'announce', 'forecast', 'news']

type Props = {
  report: QuickReport
  status: QuickReportStatus | null
  mcpSources: McpSource[]
  isHistory: boolean
  onGoToSettings: () => void
}

/** 状态点：ok=绿 / empty=空心灰 / na=红叉。
 *  注意这里的绿/红是**健康度**而不是涨跌——所以这一行里刻意不放百分数，避免语义撞车。 */
function StatusDot({ status }: { status: SectionStatus | null }) {
  if (status === 'ok') return <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
  if (status === 'empty')
    return <span className="h-1.5 w-1.5 shrink-0 rounded-full border border-zinc-400 dark:border-zinc-600" />
  return <span className="shrink-0 text-[10px] leading-none text-rose-500">✕</span>
}

/**
 * 数据源与新鲜度看板（右栏 4）。
 *
 * 两个设计约束：
 * ① **恒定渲染**——`status` 为 null（后端还没有 /status 端点，或网络抖动）时不整块隐藏，
 *    退化成「报文自带信息 + errors 按 stage 分组」，与 SettingsView 对网关不可达的处理同一纪律：
 *    运维态要被看见，不是被藏起来。
 * ② **join McpSource 是这个面板的最高价值产出**：「催化事件 na」+「服务它的源 enabled=false」
 *    ⇒「是你自己在设置页关掉了这个源」，这和「接口无权限」是完全不同的处置动作，别处推不出来。
 *    但必须可选：网关不可达时 sources 为空数组，此时以后端 src_label 为显示兜底，
 *    绝不能让空 sources 把整个 provenance 列表抹掉。
 */
export function PanelDataSource({ report, status, mcpSources, isHistory, onGoToSettings }: Props) {
  const [openReason, setOpenReason] = useState<string | null>(null)

  const sourceById = new Map(mcpSources.map((s) => [s.id, s]))
  const serverTime = status?.serverTime ?? null

  // errors 按 stage 分组：/status 不可用时用它顶替 per-section 的 reason
  const errorsByStage = new Map<string, string[]>()
  for (const e of report.errors) {
    const list = errorsByStage.get(e.stage) ?? []
    list.push(`${e.tool}：${e.reason}`)
    errorsByStage.set(e.stage, list)
  }

  const lastError = status?.lastError ?? null
  const showLastError =
    !isHistory && lastError !== null && (!status?.generatedAt || !lastError.at || lastError.at > status.generatedAt)

  return (
    <PanelShell
      icon={Database}
      title="数据源与新鲜度"
      status="ok"
      hint={status === null ? <Badge tone="neutral">状态接口不可用</Badge> : undefined}
    >
      {/* —— 时间四行 —— */}
      <dl className="mb-2.5 space-y-1">
        <Row label="数据截止日" value={<span className="rt-num">{report.date}</span>} />
        <Row
          label="生成于"
          value={
            <span>
              {formatTime(report.generated_at)}
              <span className="rt-micro ml-1.5">{formatRelative(report.generated_at, serverTime)}</span>
            </span>
          }
        />
        {!isHistory && (
          <Row
            label="下次生成"
            value={
              status === null ? (
                <span className="rt-micro">未知</span>
              ) : !status.auto ? (
                <span className="rt-micro">定时已关闭（可用计划任务调 --if-stale）</span>
              ) : status.nextRunAt ? (
                <span>
                  {formatTime(status.nextRunAt)}
                  <span className="rt-micro ml-1.5">{formatRelative(status.nextRunAt, serverTime)}</span>
                </span>
              ) : (
                <span className="rt-micro">未知</span>
              )
            }
          />
        )}
        {status?.running && <Row label="当前" value={<Badge tone="primary">正在生成…</Badge>} />}
      </dl>

      {/* —— 各段来源 —— */}
      <div className="space-y-1 border-t border-[var(--rt-line-soft)] pt-2">
        {SECTION_ORDER.map((key) => {
          const sec = report[key]
          const st = status?.sections?.[key]
          const srcId = sec.source?.id ?? st?.src ?? null
          const srcLabel = sec.source?.label ?? st?.src_label ?? null
          const srcTool = sec.source?.tool ?? st?.src_tool ?? null
          const count = st?.count ?? null
          const gatewaySrc = srcId ? sourceById.get(srcId) : undefined
          const disabledInSettings = gatewaySrc !== undefined && !gatewaySrc.enabled
          const reasons = errorsByStage.get(key) ?? []
          const isOpen = openReason === key

          return (
            <div key={key}>
              <div className="flex items-center gap-1.5">
                <StatusDot status={sec.status} />
                <span className="rt-body w-[4.5rem] shrink-0">{SECTION_LABELS[key]}</span>
                <span className="rt-micro min-w-0 flex-1 truncate">
                  {srcLabel ?? (sec.status === 'na' ? '未接入' : '来源未知')}
                  {srcTool && <span className="ml-1 opacity-70">· {srcTool}</span>}
                </span>
                {count != null && <span className="rt-num rt-micro shrink-0">{count} 行</span>}
                {reasons.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setOpenReason(isOpen ? null : key)}
                    className="shrink-0 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
                    aria-label={`查看 ${SECTION_LABELS[key]} 的取数告警`}
                  >
                    <CaretDown size={11} className={cn('transition-transform', isOpen && 'rotate-180')} />
                  </button>
                )}
              </div>
              {/* 该源被在设置页关掉了 —— 与「接口无权限」是完全不同的处置动作，必须区分开说 */}
              {disabledInSettings && (
                <div className="ml-4 mt-0.5">
                  <button type="button" onClick={onGoToSettings} className="rt-micro text-amber-600 hover:underline dark:text-amber-400">
                    ↳ 该源当前已在设置页停用 · 去开启
                  </button>
                </div>
              )}
              {/* reason 原文很长（含完整上游报错），折叠展示但**绝不整条截掉** */}
              {isOpen && (
                <ul className="ml-4 mt-1 space-y-1">
                  {reasons.map((r, i) => (
                    <li key={i} className="rt-micro break-words rounded-control bg-zinc-100/70 px-2 py-1 dark:bg-zinc-800/50">
                      {r}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>

      {/* —— 走势序列（board 子键，单独一行，失败不影响板块段本身）—— */}
      {report.board.seriesStatus !== null && (
        <div className="mt-1.5 flex items-center gap-1.5 border-t border-[var(--rt-line-soft)] pt-2">
          <StatusDot status={report.board.seriesStatus} />
          <span className="rt-body w-[4.5rem] shrink-0">指数走势</span>
          <span className="rt-micro min-w-0 flex-1 truncate">
            {report.board.series.length > 0
              ? `${report.board.series.length} 条序列 · ${report.board.series[0]?.points.length ?? 0} 个交易日`
              : '未配置或取数失败'}
          </span>
        </div>
      )}

      {/* —— 上次生成失败 —— */}
      {showLastError && (
        <div className="mt-2 rounded-control border border-amber-200/70 bg-amber-50/60 px-2 py-1.5 dark:border-amber-500/20 dark:bg-amber-500/5">
          <div className="flex items-center gap-1.5">
            <Warning size={12} className="shrink-0 text-amber-600 dark:text-amber-400" weight="fill" />
            <span className="rt-micro text-amber-700 dark:text-amber-400">
              上次生成失败 {lastError.at ? formatTime(lastError.at) : ''}
            </span>
          </div>
          <p className="rt-micro mt-1 break-words text-amber-700/90 dark:text-amber-400/90">{lastError.error}</p>
        </div>
      )}

      {/* —— 网关整体不可达时的兜底说明 —— */}
      {mcpSources.length === 0 && (
        <p className="rt-micro mt-2 opacity-80">网关源列表不可用，以上来源信息取自报文自带的 provenance。</p>
      )}
    </PanelShell>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="rt-micro w-[4.5rem] shrink-0">{label}</dt>
      <dd className="rt-body min-w-0 flex-1">{value}</dd>
    </div>
  )
}
