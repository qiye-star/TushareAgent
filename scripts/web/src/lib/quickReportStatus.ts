/**
 * GET /api/quickreport/status 的类型与容错解析（同 lib/quickReport.ts 的「解析失败返回
 * null 绝不崩」纪律）。这个端点是**实时**视角（生成中/下次触发/上次失败），与随报文归档的
 * per-section provenance（lib/quickReport.ts 的 SectionSource）职责分开：
 * - 报文内（归档、不可变）：这一份报告当时是谁取的数
 * - /status（实时、易变）：调度器现在在做什么
 *
 * 后端到今天为止可能还没部署这个端点（旧版本 404）——parseQuickReportStatus 对此返回 null，
 * 调用方（useQuickReport）据此退化为「只用 history 列表检测新报文」，不阻断其它功能。
 */

export type SectionKey = 'board' | 'watchlist' | 'announce' | 'forecast' | 'news'

export interface StatusSectionRow {
  status: 'ok' | 'empty' | 'na' | null
  src: string | null
  src_tool: string | null
  src_label: string | null
  note: string | null
  count: number | null
  fetched_at: string | null
  attempts: { source: string; tool: string; ok: boolean; reason?: string }[]
}

export interface QuickReportStatus {
  exists: boolean
  date: string | null
  generatedAt: string | null
  generatedHhmm: string
  sector: string | null
  missing: string[]
  missingRequired: string[]
  sections: Partial<Record<SectionKey, StatusSectionRow>>
  errorsCount: number
  errorsBySource: Record<string, number>
  seriesStatus: 'ok' | 'empty' | 'na' | null
  // 实时字段（与报文无关）
  serverTime: string | null
  auto: boolean
  running: boolean
  configOk: boolean
  schedule: { hour: number; minute: number; tz: string } | null
  nextRunAt: string | null
  requiredSections: string[]
  lastError: { at: string | null; error: string; stage?: string | null } | null
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}
function asStr(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}
function asOptStr(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null
}
function asNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}
function asStrList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}
function asStatusOrNull(v: unknown): 'ok' | 'empty' | 'na' | null {
  return v === 'ok' || v === 'empty' || v === 'na' ? v : null
}

const SECTION_KEYS: SectionKey[] = ['board', 'watchlist', 'announce', 'forecast', 'news']

function asSectionRow(v: unknown): StatusSectionRow {
  if (!isObj(v)) {
    return { status: null, src: null, src_tool: null, src_label: null, note: null, count: null, fetched_at: null, attempts: [] }
  }
  return {
    status: asStatusOrNull(v.status),
    src: asOptStr(v.src),
    src_tool: asOptStr(v.src_tool),
    src_label: asOptStr(v.src_label),
    note: asOptStr(v.note),
    count: asNum(v.count),
    fetched_at: asOptStr(v.fetched_at),
    attempts: Array.isArray(v.attempts)
      ? v.attempts.flatMap((a) =>
          isObj(a)
            ? [
                {
                  source: asStr(a.source),
                  tool: asStr(a.tool),
                  ok: a.ok === true,
                  ...(typeof a.reason === 'string' ? { reason: a.reason } : {}),
                },
              ]
            : [],
        )
      : [],
  }
}

/** 后端 /status 响应 → QuickReportStatus；形状不符（含 404 上游先行处理）→ null。 */
export function parseQuickReportStatus(v: unknown): QuickReportStatus | null {
  if (!isObj(v)) return null
  if (typeof v.exists !== 'boolean') return null

  const sectionsRaw = isObj(v.sections) ? v.sections : {}
  const sections: Partial<Record<SectionKey, StatusSectionRow>> = {}
  for (const k of SECTION_KEYS) {
    if (k in sectionsRaw) sections[k] = asSectionRow(sectionsRaw[k])
  }

  const errorsBySourceRaw = isObj(v.errors_by_source) ? v.errors_by_source : {}
  const errorsBySource: Record<string, number> = {}
  for (const [k, val] of Object.entries(errorsBySourceRaw)) {
    if (typeof val === 'number') errorsBySource[k] = val
  }

  const schedule = isObj(v.schedule)
    ? {
        hour: asNum(v.schedule.hour) ?? 0,
        minute: asNum(v.schedule.minute) ?? 0,
        tz: asStr(v.schedule.tz, 'Asia/Shanghai'),
      }
    : null

  const lastError = isObj(v.last_error)
    ? { at: asOptStr(v.last_error.at), error: asStr(v.last_error.error), stage: asOptStr(v.last_error.stage) }
    : null

  return {
    exists: v.exists,
    date: asOptStr(v.date),
    generatedAt: asOptStr(v.generated_at),
    generatedHhmm: asStr(v.generated_hhmm),
    sector: asOptStr(v.sector),
    missing: asStrList(v.missing),
    missingRequired: asStrList(v.missing_required),
    sections,
    errorsCount: asNum(v.errors_count) ?? 0,
    errorsBySource,
    seriesStatus: asStatusOrNull(v.series_status),
    serverTime: asOptStr(v.server_time),
    auto: v.auto === true,
    running: v.running === true,
    configOk: v.config_ok === true,
    schedule,
    nextRunAt: asOptStr(v.next_run_at),
    requiredSections: asStrList(v.required_sections),
    lastError,
  }
}

export type Freshness = 'fresh' | 'stale' | 'newer-available' | 'generating' | 'failed' | 'unknown'

/**
 * 新鲜度优先级（上者赢）：generating > failed > newer-available > stale > fresh > unknown。
 *
 * `stale`-on-missing 与后端一致：`_is_latest_complete` 把必需段缺失视为「不是最新、下次触发重试」，
 * 所以这里也不把它当终态——UI 应该说「下次触发会重试」而不是摆烂。
 */
export function deriveFreshness(args: {
  status: QuickReportStatus | null
  localGenerating: boolean
  hasNewerReport: boolean
  isHistory: boolean
}): Freshness {
  const { status, localGenerating, hasNewerReport, isHistory } = args
  if (isHistory) return 'fresh' // 历史报文本身是完整快照，不做新鲜度判断（调用方隐藏 pill）
  if (localGenerating || status?.running) return 'generating'
  if (status?.lastError && status.generatedAt && status.lastError.at && status.lastError.at > status.generatedAt) {
    return 'failed'
  }
  if (hasNewerReport) return 'newer-available'
  if (status === null) return 'unknown'
  if (!status.exists) return 'unknown'
  if (status.missingRequired.length > 0) return 'stale'
  return 'fresh'
}
