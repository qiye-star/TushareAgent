import { FileX, Sparkle } from '@phosphor-icons/react'
import { cn } from '@/lib/cn'
import { FAMILY_HINT, FAMILY_LABEL, type SkillRow } from '@/lib/skills'
import { Badge } from '@/components/ui/Badge'
import { Switch } from '@/components/ui/Switch'
import { Tooltip } from '@/components/ui/Tooltip'

/**
 * 技能卡片：点卡片看详情，右上开关控制是否参与自动路由。
 *
 * 数据源 Badge 只做标注、**不置灰不禁用**（决策 O2）——多数技能能降级到 Tushare 官方或免费源，
 * 因为某个上游源被关掉就把整张卡禁掉会误杀。
 */
export function SkillCard({
  skill,
  onOpen,
  onToggle,
}: {
  skill: SkillRow
  onOpen: () => void
  onToggle: (enabled: boolean) => void
}) {
  return (
    <div
      className={cn(
        'group flex h-full flex-col rounded-xl border border-zinc-200 bg-white p-4 transition-all',
        'hover:border-primary-300 hover:shadow-card dark:border-zinc-800 dark:bg-zinc-900/60 dark:hover:border-primary-500/40',
        !skill.enabled && 'opacity-60',
      )}
    >
      <div className="flex items-start gap-2">
        <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
          <div className="truncate text-[14.5px] font-semibold text-zinc-800 group-hover:text-primary-700 dark:text-zinc-100 dark:group-hover:text-primary-300">
            {skill.name}
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-400 dark:text-zinc-500">
            {skill.rawName}
          </div>
        </button>
        <Tooltip content={skill.enabled ? '停用后不再参与自动路由' : '启用以参与自动路由'}>
          <span className="shrink-0 pt-0.5">
            <Switch checked={skill.enabled} onCheckedChange={onToggle} />
          </span>
        </Tooltip>
      </div>

      <button type="button" onClick={onOpen} className="mt-2 flex-1 text-left">
        <p className="line-clamp-3 text-[12.5px] leading-relaxed text-zinc-500 dark:text-zinc-400">
          {skill.catalogLine}
        </p>
      </button>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Badge tone="neutral">{skill.domainLabel}</Badge>
        {skill.shouldRag && (
          <Tooltip content="命中时并行检索年报知识库">
            <Badge tone="primary">
              <Sparkle size={10} weight="fill" />
              年报检索
            </Badge>
          </Tooltip>
        )}
        {skill.filesLimited && (
          <Tooltip content="本环境无 Excel/PPT 生成工具，只输出内容与结构建议">
            <Badge tone="error">
              <FileX size={10} />
              文件受限
            </Badge>
          </Tooltip>
        )}
        {skill.sourceFamilies
          .filter((f) => f !== 'free')
          .map((f) => (
            <Tooltip key={f} content={FAMILY_HINT}>
              <Badge tone="neutral">{FAMILY_LABEL[f]}</Badge>
            </Tooltip>
          ))}
      </div>
    </div>
  )
}
