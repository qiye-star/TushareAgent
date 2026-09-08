/**
 * 快报（quickreport）六段式报告的 TS 类型与容错解析。
 *
 * 镜像后端 demomcp/quickreport/pipeline.py 输出的 JSON 契约：
 * 顶层元信息 + board/watchlist/announce/forecast/news/brief 六段；
 * 段 status 三态：ok（有内容）/ empty（本期无）/ na（数据未接入）。
 * 数值一律「数值 + 文本」双份（pct + pct_text），null 由渲染层显示 '—'。
 *
 * parseQuickReport 为全站「解析失败返回 null 绝不崩」模式（同 lib/table.ts 的 parseToolTable）：
 * 任何字段形状不符 → null，调用方走空态/错误态，绝不裸渲染 undefined。
 */

export type SectionStatus = 'ok' | 'empty' | 'na'

export interface BoardRow {
  name: string
  pct: number | null
  pct_text: string | null
  inflow: number | null
  inflow_text: string | null
  remark: string
  provider: string
}

export interface TopInflowItem {
  rank: number
  name: string
  inflow: number | null
  inflow_text: string | null
}

export interface PoolInflow {
  value: number | null
  text: string | null
}

export interface BoardSection {
  status: SectionStatus
  note: string | null
  rows: BoardRow[]
  top_inflow: TopInflowItem[]
  pool_inflow: PoolInflow | null
}

export interface WatchRow {
  name: string
  ts_code: string
  pct: number | null
  pct_text: string | null
  close: number | null
  close_text: string | null
  week_pct: number | null
  week_pct_text: string | null
  turnover: number | null
  market_cap: number | null
  remark: string
}

export interface WatchlistSection {
  status: SectionStatus
  note: string | null
  pool_count: number
  row_count: number
  truncated: boolean
  total_count: number
  missing_codes: number
  rows: WatchRow[]
}

export interface AnnounceItem {
  ts_code: string
  name: string
  type: string
  title: string
  ann_date: string
}

export interface AnnounceSection {
  status: SectionStatus
  note: string | null
  limit: number
  items: AnnounceItem[]
}

export interface ForecastItem {
  ts_code: string
  name: string
  scope: string
  yoy_min: number | null
  yoy_max: number | null
  yoy_text: string
  hits: string[]
  reason: string
}

export interface ForecastSection {
  status: SectionStatus
  note: string | null
  thresholds: { up: number; down: number }
  hit_count: number
  items: ForecastItem[]
}

export interface NewsItem {
  title: string
  src: string
  datetime: string
  url: string
}

export interface NewsSection {
  status: SectionStatus
  note: string | null
  items: NewsItem[]
}

export interface BriefSection {
  text: string
  chars: number
}

export interface QuickReportError {
  stage: string
  tool: string
  reason: string
}

export interface QuickReport {
  version: number
  generated_at: string
  date: string
  sector: string
  missing: string[]
  errors: QuickReportError[]
  board: BoardSection
  watchlist: WatchlistSection
  announce: AnnounceSection
  forecast: ForecastSection
  news: NewsSection
  brief: BriefSection
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function asStr(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

function asNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function asStatus(v: unknown): SectionStatus | null {
  return v === 'ok' || v === 'empty' || v === 'na' ? v : null
}

function asList(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

function asStrList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

function secStatus(v: unknown): SectionStatus | null {
  return isObj(v) ? asStatus(v.status) : null
}

/**
 * 后端快报 JSON → QuickReport；形状不符返回 null（调用方走空态）。
 * 校验宽松：仅检查关键字段存在与类型，允许缺省的可选字段（如 note）。
 */
export function parseQuickReport(v: unknown): QuickReport | null {
  if (!isObj(v)) return null
  if (typeof v.generated_at !== 'string' || typeof v.date !== 'string') return null
  if (secStatus(v.board) === null) return null
  if (secStatus(v.watchlist) === null) return null
  if (secStatus(v.announce) === null) return null
  if (secStatus(v.forecast) === null) return null
  if (secStatus(v.news) === null) return null
  if (!isObj(v.brief) || typeof (v.brief as Record<string, unknown>).text !== 'string') return null

  /** 提取段的行数组：board/watchlist 用 'rows'，announce/forecast/news 用 'items'（key 由调用处指定）。
   *  （历史 bug：三参版 itemKey 从未传入 → 三段 items 恒空 → 前端显示「本期无…」假空态，2026-09-05 修复） */
  const sec = (x: unknown, key: 'rows' | 'items') => {
    if (!isObj(x)) return { status: 'na' as SectionStatus, note: null as string | null, _rows: [] as unknown[] }
    const status = asStatus(x.status) ?? 'na'
    const note = x.note == null ? null : asStr(x.note, null as unknown as string)
    return {
      status,
      note,
      _rows: asList(x[key] as unknown),
    }
  }

  const board = sec(v.board as unknown, 'rows')
  const watch = sec(v.watchlist as unknown, 'rows')
  const ann = sec(v.announce as unknown, 'items')
  const fc = sec(v.forecast as unknown, 'items')
  const news = sec(v.news as unknown, 'items')

  const boardObj = v.board as Record<string, unknown>
  const watchObj = v.watchlist as Record<string, unknown>
  const annObj = v.announce as Record<string, unknown>
  const fcObj = v.forecast as Record<string, unknown>
  const newsObj = v.news as Record<string, unknown>

  return {
    version: typeof v.version === 'number' ? v.version : 1,
    generated_at: v.generated_at,
    date: v.date,
    sector: asStr(v.sector, 'AI算力产业链'),
    missing: asStrList(v.missing),
    errors: asList(v.errors).flatMap((e) => (isObj(e) ? [{ stage: asStr(e.stage), tool: asStr(e.tool), reason: asStr(e.reason) }] : [])),
    board: {
      status: board.status,
      note: board.note,
      rows: board._rows
        .filter(isObj)
        .map((r) => ({
          name: asStr(r.name),
          pct: asNum(r.pct),
          pct_text: r.pct_text == null ? null : asStr(r.pct_text, null as unknown as string),
          inflow: asNum(r.inflow),
          inflow_text: r.inflow_text == null ? null : asStr(r.inflow_text, null as unknown as string),
          remark: asStr(r.remark),
          provider: asStr(r.provider),
        })),
      top_inflow: asList(boardObj.top_inflow as unknown)
        .filter(isObj)
        .map((r, i) => ({
          rank: typeof r.rank === 'number' ? r.rank : i + 1,
          name: asStr(r.name),
          inflow: asNum(r.inflow),
          inflow_text: r.inflow_text == null ? null : asStr(r.inflow_text, null as unknown as string),
        })),
      pool_inflow: isObj(boardObj.pool_inflow)
        ? {
            value: asNum((boardObj.pool_inflow as Record<string, unknown>).value),
            text:
              (boardObj.pool_inflow as Record<string, unknown>).text == null
                ? null
                : asStr((boardObj.pool_inflow as Record<string, unknown>).text, null as unknown as string),
          }
        : null,
    },
    watchlist: {
      status: watch.status,
      note: watch.note,
      pool_count: typeof watchObj.pool_count === 'number' ? watchObj.pool_count : 0,
      row_count: typeof watchObj.row_count === 'number' ? watchObj.row_count : 0,
      truncated: watchObj.truncated === true,
      total_count: typeof watchObj.total_count === 'number' ? watchObj.total_count : 0,
      missing_codes: typeof watchObj.missing_codes === 'number' ? watchObj.missing_codes : 0,
      rows: watch._rows
        .filter(isObj)
        .map((r) => ({
          name: asStr(r.name),
          ts_code: asStr(r.ts_code),
          pct: asNum(r.pct),
          pct_text: r.pct_text == null ? null : asStr(r.pct_text, null as unknown as string),
          close: asNum(r.close),
          close_text: r.close_text == null ? null : asStr(r.close_text, null as unknown as string),
          week_pct: asNum(r.week_pct),
          week_pct_text: r.week_pct_text == null ? null : asStr(r.week_pct_text, null as unknown as string),
          turnover: asNum(r.turnover),
          market_cap: asNum(r.market_cap),
          remark: asStr(r.remark),
        })),
    },
    announce: {
      status: ann.status,
      note: ann.note,
      limit: typeof annObj.limit === 'number' ? annObj.limit : 20,
      items: ann._rows
        .filter(isObj)
        .map((r) => ({
          ts_code: asStr(r.ts_code),
          name: asStr(r.name),
          type: asStr(r.type, '其他'),
          title: asStr(r.title),
          ann_date: asStr(r.ann_date),
        })),
    },
    forecast: {
      status: fc.status,
      note: fc.note,
      thresholds: isObj(fcObj.thresholds)
        ? { up: asNum((fcObj.thresholds as Record<string, unknown>).up) ?? 50, down: asNum((fcObj.thresholds as Record<string, unknown>).down) ?? -20 }
        : { up: 50, down: -20 },
      hit_count: typeof fcObj.hit_count === 'number' ? fcObj.hit_count : 0,
      items: fc._rows
        .filter(isObj)
        .map((r) => ({
          ts_code: asStr(r.ts_code),
          name: asStr(r.name),
          scope: asStr(r.scope),
          yoy_min: asNum(r.yoy_min),
          yoy_max: asNum(r.yoy_max),
          yoy_text: asStr(r.yoy_text, '—'),
          hits: asStrList(r.hits),
          reason: asStr(r.reason),
        })),
    },
    news: {
      status: news.status,
      note: news.note,
      items: news._rows
        .filter(isObj)
        .map((r) => ({
          title: asStr(r.title),
          src: asStr(r.src),
          datetime: asStr(r.datetime),
          url: asStr(r.url),
        })),
    },
    brief: {
      text: (v.brief as Record<string, unknown>).text as string,
      chars: typeof (v.brief as Record<string, unknown>).chars === 'number' ? ((v.brief as Record<string, unknown>).chars as number) : 0,
    },
  }
}
