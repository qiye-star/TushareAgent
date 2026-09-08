import { useMemo, useState } from 'react'
import { MagnifyingGlass, Sparkle } from '@phosphor-icons/react'
import { useSkills } from '@/hooks/useSkills'
import { cn } from '@/lib/cn'
import type { SkillRow } from '@/lib/skills'
import { Skeleton } from '@/components/ui/Skeleton'
import { SkillCard } from './SkillCard'
import { SkillDetail } from './SkillDetail'

/**
 * 技能页：投研报告技能库（claude-for 63 条 + 内置快报）。
 *
 * 卡片 → 详情侧滑 → 「快速使用」跳回对话页并在输入框挂上技能芯片（强制本轮使用）。
 * 每张卡右上的开关控制该技能是否参与**自动路由**（停用后 router 清单里就没有它）。
 */
export function SkillsView({ onQuickUse }: { onQuickUse: (skill: SkillRow) => void }) {
  const { skills, domains, loading, error, toggle } = useSkills()
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState<string | null>(null)
  const [active, setActive] = useState<SkillRow | null>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return skills.filter((s) => {
      if (domain && s.domain !== domain) return false
      if (!q) return true
      return (
        s.name.toLowerCase().includes(q) ||
        s.rawName.toLowerCase().includes(q) ||
        s.catalogLine.toLowerCase().includes(q)
      )
    })
  }, [skills, query, domain])

  const grouped = useMemo(() => {
    const map = new Map<string, { label: string; rows: SkillRow[] }>()
    for (const s of filtered) {
      const entry = map.get(s.domain) ?? { label: s.domainLabel, rows: [] }
      entry.rows.push(s)
      map.set(s.domain, entry)
    }
    return [...map.entries()]
  }, [filtered])

  const enabledCount = skills.filter((s) => s.enabled).length

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-zinc-200 bg-white px-6 py-4 dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="flex items-center gap-2 text-[17px] font-semibold tracking-tight text-zinc-800 dark:text-zinc-100">
              <Sparkle size={18} weight="fill" className="text-primary-500" />
              投研技能
            </h1>
            <p className="mt-0.5 text-[12.5px] text-zinc-500 dark:text-zinc-400">
              {loading ? '加载中…' : `共 ${skills.length} 个技能 · 已启用 ${enabledCount} 个`}
              　·　点技能看详情，「快速使用」直接带着它去对话
            </p>
          </div>
          <div className="relative">
            <MagnifyingGlass
              size={15}
              className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索技能…"
              className="w-56 rounded-lg border border-zinc-300 bg-white py-1.5 pl-8 pr-3 text-[13px] text-zinc-800 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-500/20 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
            />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          <DomainChip active={domain === null} onClick={() => setDomain(null)}>
            全部 {skills.length}
          </DomainChip>
          {domains.map((d) => (
            <DomainChip key={d.id} active={domain === d.id} onClick={() => setDomain(d.id)}>
              {d.label} {d.count}
            </DomainChip>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {error && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-[13px] text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </div>
        )}

        {loading && (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-32" />
            ))}
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="py-16 text-center text-[13px] text-zinc-400 dark:text-zinc-500">
            {skills.length === 0
              ? '技能库为空（SKILL_LIBRARY_ENABLED=false 或语料缺失）'
              : '没有匹配的技能'}
          </div>
        )}

        {grouped.map(([id, { label, rows }]) => (
          <section key={id} className="mb-7">
            <div className="mb-2.5 flex items-center gap-2">
              <h2 className="text-[13px] font-semibold text-zinc-700 dark:text-zinc-200">{label}</h2>
              <span className="text-[11px] text-zinc-400 dark:text-zinc-500">{rows.length}</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {rows.map((s) => (
                <SkillCard
                  key={s.id}
                  skill={s}
                  onOpen={() => setActive(s)}
                  onToggle={(v) => void toggle(s.id, v)}
                />
              ))}
            </div>
          </section>
        ))}
      </div>

      <SkillDetail
        skill={active ? (skills.find((s) => s.id === active.id) ?? active) : null}
        open={active !== null}
        onClose={() => setActive(null)}
        onQuickUse={onQuickUse}
        onToggle={(id, v) => void toggle(id, v)}
      />
    </div>
  )
}

function DomainChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full px-2.5 py-1 text-[12px] font-medium transition-colors',
        active
          ? 'bg-primary-600 text-white'
          : 'bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700',
      )}
    >
      {children}
    </button>
  )
}
