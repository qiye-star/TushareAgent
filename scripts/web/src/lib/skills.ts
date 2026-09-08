/**
 * 报告技能库（claude-for vendored 语料）的前端类型与容错解析。
 *
 * 与 lib/quickReport.ts 同一套哲学：后端字段缺失/类型不符时**退化成安全默认值**，绝不让一次解析
 * 把技能页整块打空。类型刻意不放进 lib/types.ts（那个文件并发改动多）。
 */

export type SkillSourceFamily = 'wind' | 'ifind' | 'free'

export type SkillRow = {
  id: string
  /** 中文短名（skill_catalog.py 维护） */
  name: string
  /** upstream 目录名/frontmatter name，如 china-dcf */
  rawName: string
  domain: string
  domainLabel: string
  /** router 清单用的一句话短描述 */
  catalogLine: string
  reportType: string
  enabled: boolean
  /** 正文涉及 Excel/PPT 等文件产物 → 本环境只能输出内容与结构建议 */
  filesLimited: boolean
  /** 命中时会并行检索年报知识库 */
  shouldRag: boolean
  sourceFamilies: SkillSourceFamily[]
}

export type SkillDomain = { id: string; label: string; count: number }

export type SkillDetail = SkillRow & {
  /** upstream 原始 description（含中英文触发词） */
  description: string
  /** SKILL.md 正文原文，详情页用 react-markdown 渲染 */
  bodyMarkdown: string
  toolMapping: { old: string; new: string }[]
  capabilityNote: string | null
  toolFamilies: string[]
}

const str = (v: unknown, fallback = ''): string => (typeof v === 'string' ? v : fallback)
const bool = (v: unknown): boolean => v === true

function parseFamilies(v: unknown): SkillSourceFamily[] {
  if (!Array.isArray(v)) return []
  return v.filter((x): x is SkillSourceFamily => x === 'wind' || x === 'ifind' || x === 'free')
}

export function parseSkillRow(raw: unknown): SkillRow | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const id = str(r.id)
  if (!id) return null
  return {
    id,
    name: str(r.name, id),
    rawName: str(r.raw_name, id),
    domain: str(r.domain),
    domainLabel: str(r.domain_label, str(r.domain)),
    catalogLine: str(r.catalog_line),
    reportType: str(r.report_type),
    enabled: r.enabled !== false, // 缺字段按启用（与后端「未记录 = 启用」一致）
    filesLimited: bool(r.files_limited),
    shouldRag: bool(r.should_rag),
    sourceFamilies: parseFamilies(r.source_families),
  }
}

export function parseSkillList(raw: unknown): { skills: SkillRow[]; domains: SkillDomain[] } {
  const body = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const skills = (Array.isArray(body.skills) ? body.skills : [])
    .map(parseSkillRow)
    .filter((s): s is SkillRow => s !== null)
  const domains = (Array.isArray(body.domains) ? body.domains : [])
    .map((d) => {
      const o = (d && typeof d === 'object' ? d : {}) as Record<string, unknown>
      const id = str(o.id)
      return id ? { id, label: str(o.label, id), count: typeof o.count === 'number' ? o.count : 0 } : null
    })
    .filter((d): d is SkillDomain => d !== null)
  return { skills, domains }
}

export function parseSkillDetail(raw: unknown): SkillDetail | null {
  const row = parseSkillRow(raw)
  if (!row) return null
  const r = raw as Record<string, unknown>
  const mapping = (Array.isArray(r.tool_mapping) ? r.tool_mapping : [])
    .map((m) => {
      const o = (m && typeof m === 'object' ? m : {}) as Record<string, unknown>
      const oldName = str(o.old)
      return oldName ? { old: oldName, new: str(o.new) } : null
    })
    .filter((m): m is { old: string; new: string } => m !== null)
  return {
    ...row,
    description: str(r.description),
    bodyMarkdown: str(r.body_markdown),
    toolMapping: mapping,
    capabilityNote: typeof r.capability_note === 'string' ? r.capability_note : null,
    toolFamilies: Array.isArray(r.tool_families) ? r.tool_families.map((x) => str(x)).filter(Boolean) : [],
  }
}

/**
 * 数据源标签（决策 O2：只标不置灰）。
 *
 * 措辞刻意是「涉及」而非「需要」：实测 58/63 的正文都把 iFind 写成 Tier-1、29/63 提到 Wind，
 * 但它们几乎都能降级到 Tushare 官方或免费源 —— 写「需同花顺」既是噪声（92% 的卡片都有），
 * 也会误导用户以为关掉该源技能就不能用。
 */
export const FAMILY_LABEL: Record<SkillSourceFamily, string> = {
  wind: '万得',
  ifind: '同花顺',
  free: '免费源',
}

export const FAMILY_HINT =
  '技能正文引用了该数据源的接口；本环境会优先用 Tushare 官方等价接口，取不到再退到它。'
