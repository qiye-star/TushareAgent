import { useMemo, useState } from 'react'
import { BracketsCurly, Brain, CaretDown, CircleNotch, PaintBrush, Sliders, Terminal } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'
import { prettyJson, toolLabel } from '@/lib/format'
import type { Step } from '@/lib/types'
import { RetrievalFunnel } from './RetrievalFunnel'
import { ToolCallCard } from './ToolCallCard'

type Group =
  | { type: 'tool'; name: string; input: any; result: any }
  | { type: 'meta'; step: Step }

function groupSteps(steps: Step[]): Group[] {
  const out: Group[] = []
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i]
    if (s.kind === 'tool_call') {
      const next = steps[i + 1]
      const result =
        next && next.kind === 'tool_result' ? { ...next.data } : null
      out.push({ type: 'tool', name: s.data?.name ?? '', input: s.data?.input ?? {}, result })
      if (result) i++
    } else {
      out.push({ type: 'meta', step: s })
    }
  }
  return out
}

function MetaRow({ step, streaming }: { step: Step; streaming?: boolean }) {
  const [open, setOpen] = useState(false)
  const { kind, label, data } = step

  // RAG 漏斗：可视化检索→重排管线（代替普通 summary 行）
  if (kind === 'funnel') return <RetrievalFunnel data={data} />

  let summary = ''
  if (kind === 'intent') summary = `意图：${data.intent ?? '未知'} · 策略：${data.strategy ?? 'auto'}`
  else if (kind === 'plan') summary = `拟用工具：${(data.tools ?? []).join('、') || '无'}`
  else if (kind === 'retrieval') summary = streaming ? '' : `策略 ${data.strategy ?? ''} · ${data.chunks ?? 0} 块 · ${(data.sources ?? []).length} 来源`
  else if (kind === 'validation') summary = (data.errors ?? []).length ? `${data.errors.length} 项校验提示` : '参数校验通过'
  else if (kind === 'aggregate') summary = `工具 ${data.tool ?? 0} · RAG ${data.rag ?? 0} · 合计 ${data.total ?? 0}`
  else if (kind === 'params' || kind === 'stage') summary = '查看详情'
  else summary = label

  const hasJson = kind === 'params' || kind === 'stage'
  const isRetrievalPending = kind === 'retrieval' && streaming
  const icon =
    kind === 'intent' ? <Sliders size={14} /> :
    kind === 'retrieval' ? <BracketsCurly size={14} /> :
    <PaintBrush size={14} />

  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => hasJson && setOpen((v) => !v)}
        className={cn(
          'flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-zinc-600 dark:text-zinc-300',
          hasJson ? 'cursor-pointer transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800' : 'cursor-default',
        )}
      >
        <span className="text-primary-500">{icon}</span>
        <span className="font-medium text-zinc-700 dark:text-zinc-200">{label}</span>
        {isRetrievalPending ? (
          <span className="ml-auto inline-flex items-center gap-1 text-[12px] text-zinc-400 dark:text-zinc-500">
            <CircleNotch size={12} className="animate-spin text-primary-500" />
            检索中…
          </span>
        ) : (
          summary && <span className="ml-auto truncate text-[12px] text-zinc-400 dark:text-zinc-500">{summary}</span>
        )}
        {hasJson && (
          <CaretDown size={13} className={cn('shrink-0 text-zinc-400 transition-transform', open && 'rotate-180')} />
        )}
      </button>
      {hasJson && open && (
        <pre className="mx-3 mb-1.5 overflow-x-auto whitespace-pre-wrap rounded-lg bg-zinc-50 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
          {prettyJson(data)}
        </pre>
      )}
    </div>
  )
}

function paramsPreview(input: unknown): string {
  if (!input || typeof input !== 'object') return ''
  const s = Object.entries(input as Record<string, unknown>)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' · ')
  return s.length > 60 ? `${s.slice(0, 60)}…` : s
}

function stepDetail(step: Step): string {
  const d = step.data ?? {}
  switch (step.kind) {
    case 'intent':
      return `意图：${d.intent ?? '未知'} · 策略：${d.strategy ?? 'auto'}`
    case 'plan':
      return `拟用工具：${(d.tools ?? []).join('、') || '无'}`
    case 'rewrite':
      return d.rewritten ? `改写：${d.rewritten}` : '已改写'
    case 'retrieval':
      return `策略 ${d.strategy ?? ''} · ${d.chunks ?? 0} 块 · ${(d.sources ?? []).length} 来源`
    case 'funnel':
      return `候选${d.recall_raw ?? '–'} › 融合${d.fused ?? '–'} › 重排${d.reranked ?? '–'} › 完成${d.final ?? '–'}`
    case 'validation':
      return (d.errors ?? []).length ? `${d.errors.length} 项校验提示` : '参数校验通过'
    case 'aggregate':
      return `工具 ${d.tool ?? 0} · RAG ${d.rag ?? 0} · 合计 ${d.total ?? 0}`
    case 'params':
      return '请求参数'
    case 'stage':
      return `阶段 · ${d.stage ?? ''}`
    default:
      return ''
  }
}

function FeedLine({ g }: { g: Group }) {
  if (g.type === 'tool') {
    const params = paramsPreview(g.input)
    return (
      <div className="flex items-start gap-1.5">
        <Terminal size={12} className="mt-0.5 shrink-0 text-primary-500" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[11.5px] font-medium text-zinc-600 dark:text-zinc-300">
            {toolLabel(g.name)}
          </div>
          {params && (
            <div className="truncate text-[11px] text-zinc-400 dark:text-zinc-500">{params}</div>
          )}
        </div>
        {g.result && (
          <span
            className={cn(
              'mt-0.5 shrink-0 text-[10.5px] font-medium',
              g.result.ok ? 'text-emerald-500' : 'text-rose-500',
            )}
          >
            {g.result.ok ? '成功' : '失败'}
          </span>
        )}
      </div>
    )
  }

  const icon =
    g.step.kind === 'intent' ? (
      <Sliders size={12} />
    ) : g.step.kind === 'retrieval' ? (
      <BracketsCurly size={12} />
    ) : (
      <PaintBrush size={12} />
    )
  const detail = stepDetail(g.step)

  return (
    <div className="flex items-start gap-1.5">
      <span className="mt-0.5 shrink-0 text-primary-500">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[11.5px] font-medium text-zinc-600 dark:text-zinc-300">
          {g.step.label}
        </div>
        {detail && (
          <div className="truncate text-[11px] text-zinc-400 dark:text-zinc-500">{detail}</div>
        )}
      </div>
    </div>
  )
}

function ThinkingFeed({ groups, streaming }: { groups: Group[]; streaming?: boolean }) {
  const recent = groups.map((g, i) => ({ g, key: i })).slice(-3)
  return (
    <div
      className="flex max-h-[4.25rem] flex-col justify-end gap-1.5 overflow-hidden px-3 pb-2.5 pt-1"
      style={{
        maskImage: 'linear-gradient(to top, #000 82%, transparent)',
        WebkitMaskImage: 'linear-gradient(to top, #000 82%, transparent)',
      }}
    >
      {recent.map(({ g, key }) => (
        <div key={key} className="funnel-stage">
          <FeedLine g={g} />
        </div>
      ))}
      {streaming && groups.length === 0 && (
        <div className="flex items-center gap-1.5 text-[11px] text-zinc-400 dark:text-zinc-500">
          <CircleNotch size={12} className="animate-spin text-primary-500" />
          思考中…
        </div>
      )}
    </div>
  )
}

export function ThinkingTrace({
  steps,
  thinking,
  streaming,
}: {
  steps: Step[]
  thinking: string
  streaming?: boolean
}) {
  const [open, setOpen] = useState(false)
  const groups = useMemo(() => groupSteps(steps), [steps])
  const toolCount = groups.filter((g) => g.type === 'tool').length

  if (groups.length === 0 && !thinking) return null

  return (
    <div className="rounded-xl border border-zinc-200/70 bg-zinc-50/50 dark:border-zinc-800 dark:bg-zinc-900/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-[13px] text-zinc-500 transition-colors hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
      >
        <Brain size={15} className="text-primary-500" />
        <span className="font-medium">思考过程</span>
        <span className="ml-auto flex min-w-0 items-center gap-2">
          {toolCount > 0 && (
            <span className="rounded-full bg-zinc-200 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              {toolCount} 次工具
            </span>
          )}
          <CaretDown
            size={14}
            className={cn('shrink-0 transition-transform', open && 'rotate-180')}
          />
        </span>
      </button>

      {!open && (groups.length > 0 || streaming) && (
        <ThinkingFeed groups={groups} streaming={streaming} />
      )}

      {open && (
        <div className="border-t border-zinc-200/70 dark:border-zinc-800">
          {thinking && (
            <div className="px-3 py-2 text-[13px] leading-relaxed text-zinc-500 italic dark:text-zinc-400">
              {thinking}
            </div>
          )}
          <div className="space-y-2 px-3 py-3">
            {groups.map((g, i) =>
              g.type === 'tool' ? (
                <ToolCallCard key={i} name={g.name} input={g.input} result={g.result} />
              ) : (
                <MetaRow key={i} step={g.step} streaming={streaming} />
              ),
            )}
          </div>
        </div>
      )}
    </div>
  )
}
