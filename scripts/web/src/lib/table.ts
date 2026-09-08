// 工具返回文本 → 表格模型。整个前端只此一份解析逻辑，
// 引用来源卡（CitationList）与思考过程工具卡（ToolCallCard）共用，保证同一份数据两处呈现一致。
//
// 覆盖 Tushare 官方 MCP / 本地 mcp_server 代理 / 万得实际会返回的形态（已核对 demo.db 里的真实 tool 消息）：
//   1. 裸数组 [{...}, ...]                      —— 主力形态（stock_basic / daily / income / fina_indicator…）
//   2. {data: {columns: [{name,type}], rows: [[…]]}} —— 美股/实时 K 线的列信封
//   3. {data: [{...}]} / {code,msg,row_count,data:[…]} —— 本地代理契约
//   4. 裸对象 {...}                              —— 单行
// 不是表格的一律返回 null，由调用方回退成纯文本渲染（绝不空白）：
//   {data:{items:[{content:"markdown"}]}}（公告/研报正文）、权限提示纯文本、"[]"、JSON 解析失败。

/** 单元格原始值（渲染层再决定对齐与千分位）。 */
export type Cell = string | number | boolean | null

export type DataTableModel = {
  columns: string[]
  rows: Cell[][]
  /** 行数超过 MAX_ROWS 被截断（UI 需提示"仅显示前 N 行"）。 */
  truncated: boolean
  /** 截断前的真实总行数。 */
  totalRows: number
}

/** 单表最多渲染行数——纯前端保护，避免一次几千行把主线程钉死。 */
export const MAX_ROWS = 500

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** 把值压成可直接渲染的单元格；嵌套结构退化成 JSON 串而不是 "[object Object]"。 */
function toCell(v: unknown): Cell {
  if (v === null || v === undefined) return null
  if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'string') return v
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

/** 行 dict 列表 → 模型；列顺序取各行 key 的首见顺序并集（不同行字段不齐也不丢列）。 */
function fromRecords(records: Record<string, unknown>[]): DataTableModel | null {
  if (!records.length) return null
  const columns: string[] = []
  const seen = new Set<string>()
  for (const r of records) {
    for (const k of Object.keys(r)) {
      if (!seen.has(k)) {
        seen.add(k)
        columns.push(k)
      }
    }
  }
  if (!columns.length) return null
  const totalRows = records.length
  const rows = records.slice(0, MAX_ROWS).map((r) => columns.map((c) => toCell(r[c])))
  return { columns, rows, truncated: totalRows > MAX_ROWS, totalRows }
}

/** {columns:[{name,type}|string], rows:[[…]]} 列信封 → 模型。 */
function fromColumnEnvelope(env: Record<string, unknown>): DataTableModel | null {
  const rawCols = env.columns
  const rawRows = env.rows
  if (!Array.isArray(rawCols) || !Array.isArray(rawRows)) return null
  const columns = rawCols.map((c, i) => {
    if (typeof c === 'string') return c
    if (isPlainObject(c) && typeof c.name === 'string') return c.name
    return `列${i + 1}`
  })
  if (!columns.length) return null
  const bodyRows = rawRows.filter(Array.isArray) as unknown[][]
  if (!bodyRows.length) return null
  const totalRows = bodyRows.length
  const rows = bodyRows
    .slice(0, MAX_ROWS)
    .map((r) => columns.map((_, i) => toCell(r[i])))
  return { columns, rows, truncated: totalRows > MAX_ROWS, totalRows }
}

/**
 * 解析工具返回文本；不是表格返回 null。
 * 注意：后端 structured.sources[].data 有 20000 字上限，恰好截在 JSON 中途时
 * JSON.parse 会失败 → 这里返回 null，调用方回退纯文本，属预期的安全退化。
 */
export function parseToolTable(content: string | null | undefined): DataTableModel | null {
  const text = (content ?? '').trim()
  if (!text || text === '[]') return null

  let body: unknown
  try {
    body = JSON.parse(text)
  } catch {
    return null // 纯文本（权限提示 / 上游报错）
  }

  // 形态 1：裸数组
  if (Array.isArray(body)) {
    return fromRecords(body.filter(isPlainObject))
  }
  if (!isPlainObject(body)) return null

  const data = body.data
  // 形态 3：{data: [...]}（含 {code,msg,row_count,data}）
  if (Array.isArray(data)) {
    return fromRecords(data.filter(isPlainObject))
  }
  if (isPlainObject(data)) {
    // 形态 2：{data: {columns, rows}}
    const env = fromColumnEnvelope(data)
    if (env) return env
    // {data:{items:[{content}]}} 等文本信封 → 非表格
    if ('items' in data) return null
    return fromRecords([data])
  }

  // 形态 4：裸对象单行；纯业务信封（只有 code/msg/ok/row_count）不算表格
  if (['code', 'msg', 'ok', 'row_count', 'rowcount'].some((k) => k in body)) return null
  return fromRecords([body])
}

/** 全数字 8 位串（YYYYMMDD：trade_date/ann_date/list_date/end_date…）——当数值会被千分位
 *  成 "20,241,231"，日期语义全失（2026-09-07 审查）。这类列按字符串渲染，不进数字判定。 */
function isDateLike(v: Cell): boolean {
  return typeof v === 'string' && /^\d{8}$/.test(v.trim())
}

/** 某列是否该按数字渲染（右对齐 + 千分位）：非空单元格里 ≥80% 能解析成有限数（日期形除外）。 */
export function isNumericColumn(rows: Cell[][], colIndex: number): boolean {
  let filled = 0
  let numeric = 0
  for (const r of rows) {
    const v = r[colIndex]
    if (v === null || v === '' || isDateLike(v)) continue
    filled++
    if (typeof v === 'number' ? Number.isFinite(v) : Number.isFinite(Number(v))) numeric++
  }
  return filled > 0 && numeric / filled >= 0.8
}

const NUM_FMT = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 })

/** 单元格 → 显示文本；数值列加千分位并保留 4 位小数（7.2903 / 1.108 这类精度不丢）；日期形原样。 */
export function formatCell(v: Cell, numeric: boolean): string {
  if (v === null || v === '') return '—'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (!numeric || isDateLike(v)) return String(v)
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? NUM_FMT.format(n) : String(v)
}
