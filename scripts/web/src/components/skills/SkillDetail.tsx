import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowRight, CaretRight, PaperPlaneTilt, Warning } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'
import { getSkillDetail } from '@/lib/api'
import {
  FAMILY_HINT,
  FAMILY_LABEL,
  type SkillDetail as SkillDetailData,
  type SkillRow,
} from '@/lib/skills'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { SideSheet } from '@/components/ui/SideSheet'
import { Skeleton } from '@/components/ui/Skeleton'
import { Switch } from '@/components/ui/Switch'
import { Tooltip } from '@/components/ui/Tooltip'

/**
 * 技能详情侧滑：能力介绍（SKILL.md 正文）+ 工具名对照 + 能力限制 + 「快速使用」。
 *
 * 正文用 react-markdown（不解析原始 HTML，天然防 XSS，与聊天区同一套）；**不加 remark-breaks**——
 * SKILL.md 里大量表格/代码块，硬换行会把它们撑散。
 */
export function SkillDetail({
  skill,
  open,
  onClose,
  onQuickUse,
  onToggle,
}: {
  skill: SkillRow | null
  open: boolean
  onClose: () => void
  onQuickUse: (skill: SkillRow) => void
  onToggle: (id: string, enabled: boolean) => void
}) {
  const [detail, setDetail] = useState<SkillDetailData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mappingOpen, setMappingOpen] = useState(false)

  useEffect(() => {
    if (!open || !skill) return
    let alive = true
    setDetail(null)
    setError(null)
    getSkillDetail(skill.id)
      .then((d) => {
        if (alive) setDetail(d)
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : '加载技能详情失败')
      })
    return () => {
      alive = false
    }
  }, [open, skill])

  if (!skill) return null

  return (
    <SideSheet
      open={open}
      onOpenChange={(v) => !v && onClose()}
      title={skill.name}
      description={skill.rawName}
      className="w-[min(38rem,100%)] max-w-2xl"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">{skill.domainLabel}</Badge>
        {skill.shouldRag && <Badge tone="primary">年报检索</Badge>}
        {skill.filesLimited && <Badge tone="error">文件受限</Badge>}
        {skill.sourceFamilies.map((f) => (
          <Tooltip key={f} content={FAMILY_HINT}>
            <Badge tone="neutral">{FAMILY_LABEL[f]}</Badge>
          </Tooltip>
        ))}
      </div>

      <p className="mt-3 text-[13px] leading-relaxed text-zinc-600 dark:text-zinc-300">
        {skill.catalogLine}
      </p>

      <div className="mt-4 flex items-center gap-3">
        <Button
          onClick={() => {
            onQuickUse(skill)
            onClose()
          }}
          disabled={!skill.enabled}
        >
          <PaperPlaneTilt size={15} weight="fill" />
          快速使用
        </Button>
        <Switch
          checked={skill.enabled}
          onCheckedChange={(v) => onToggle(skill.id, v)}
          label={skill.enabled ? '已启用' : '已停用'}
        />
      </div>
      {!skill.enabled && (
        <p className="mt-2 text-[12px] text-amber-600 dark:text-amber-400">
          该技能已停用：不参与自动路由，也不能快速使用。
        </p>
      )}

      {detail?.capabilityNote && (
        <div className="mt-4 flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12.5px] leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          <Warning size={16} className="mt-0.5 shrink-0" />
          <span>{detail.capabilityNote}</span>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-[12.5px] text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </div>
      )}

      {!detail && !error && (
        <div className="mt-5 space-y-2">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      )}

      {detail && detail.toolMapping.length > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => setMappingOpen((v) => !v)}
            className="flex items-center gap-1.5 text-[12.5px] font-medium text-zinc-600 hover:text-primary-600 dark:text-zinc-300"
          >
            <CaretRight size={12} className={cn('transition-transform', mappingOpen && 'rotate-90')} />
            工具名对照（{detail.toolMapping.length}）
          </button>
          {/* 直接条件渲染，不用 Collapsible：它的 grid-rows-[0fr→1fr] 动画在这个滚动容器里
              会把 1fr 解析成「可用空间」而不是内容高度，长列表被裁成两行（实测）。 */}
          {mappingOpen && (
            <div className="pt-2">
              <p className="mb-2 text-[12px] text-zinc-500 dark:text-zinc-400">
                正文里的工具名沿用 claude-for 插件环境的旧名，本环境（MCP 网关）按右侧写法调用。
              </p>
              <ul className="space-y-1.5">
                {detail.toolMapping.map((m) => (
                  <li key={m.old} className="flex items-start gap-1.5 text-[12px]">
                    <code className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                      {m.old}
                    </code>
                    <ArrowRight size={12} className="mt-1 shrink-0 text-zinc-400" />
                    <span className="min-w-0 break-all text-zinc-500 dark:text-zinc-400">{m.new}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {detail && (
        <div className="mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
            技能说明
          </div>
          <div className="prose prose-sm prose-zinc dark:prose-invert max-w-none">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                table: ({ children }) => (
                  <div className="my-3 overflow-x-auto">
                    <table className="answer-table w-full text-left text-[12px]">{children}</table>
                  </div>
                ),
              }}
            >
              {detail.bodyMarkdown}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </SideSheet>
  )
}
