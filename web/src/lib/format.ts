import type { CitationCard, SourceCard, Step } from './types'

/** Prettify a tool-args object as readable JSON. */
export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? '{}'
  } catch {
    return String(value)
  }
}

/**
 * 把答案里残留的内联来源痕迹（工具名 / 来源标记 / 年报页）剔除，正文只留结论与分析。
 * 保守匹配：含「年报/报告/财报」的方括号片段，以及 [工具:…]/[来源:…]/[接口:…]（中英文冒号均可）。
 * 不删免责声明、不动正常数字加粗等 markdown。
 */
export function cleanAnswer(answer: string): string {
  if (!answer) return answer
  let s = answer
  s = s.replace(/\[[^\]]{0,60}?(?:年报|报告|财报)[^\]]{0,60}\]/g, '')
  s = s.replace(/\[(?:工具|来源|接口|数据)[：:][^\]]*\]/g, '')
  s = s.replace(/\[\d+\]/g, '') // 去掉 [1]/[2]/[7] 这类来源编号
  s = s.replace(/[ \t]{2,}/g, ' ')
  s = s.replace(/\n[ \t]*\n[ \t]*\n+/g, '\n\n') // 折叠多余空行
  return s.trim()
}

/** Build a Step with a shared label, used by both live streaming and reload. */
export function stepOf(kind: string, data: any): Step {
  const map: Record<string, string> = {
    intent: '意图分类',
    plan: '工具选择',
    rewrite: '改写问题',
    retrieval: '知识库检索',
    funnel: '检索重排',
    params: '请求参数',
    validation: '校验',
    aggregate: '聚合统计',
    stage: data?.stage ? `阶段 · ${data.stage}` : '阶段',
    tool_call: toolLabel(data?.name),
    tool_result: toolLabel(data?.name),
  }
  return { kind: kind as Step['kind'], label: map[kind] ?? kind, data }
}

/** Short human label per tool name / process step. */
export function toolLabel(name: string): string {
  const known: Record<string, string> = {
    stock_realtime: '实时行情',
    stock_daily: '日线行情',
    stock_daily_basic: '每日指标',
    stock_financial: '财务数据',
    stock_compare: '财务对比',
    rag_query: '财报检索',
  }
  return known[name] || name
}

/** Derive the short subtitle for a citation/source card. */
export function citeSubtitle(
  c: CitationCard | SourceCard,
): string {
  const parts: string[] = []
  if (c.type === 'rag') {
    if (c.company) parts.push(c.company)
    if (c.year) parts.push(`${c.year} 年报`)
    if ('page' in c && c.page) parts.push(`第 ${c.page} 页`)
    if ('section' in c && c.section) parts.push(c.section)
  } else {
    if ('params' in c && c.params) {
      const ts = (c.params as any).ts_code
      if (ts) parts.push(String(ts))
    } else if ('data' in c && c.data) {
      parts.push('调用结果')
    }
  }
  return parts.join(' · ')
}

/** Truncate a session title to a display length. */
export function truncate(text: string, max = 18): string {
  const t = text.trim().replace(/\s+/g, ' ')
  return t.length > max ? `${t.slice(0, max)}…` : t
}

/** Best-effort first user message as a session title. */
export function deriveTitle(content: string | null): string {
  if (!content) return '新会话'
  return truncate(content)
}

export function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Map a DeepSeek stopped_reason to a friendly Chinese label. */
export function stoppedReasonLabel(reason: string | null): string {
  const map: Record<string, string> = {
    end_turn: '已完成',
    tool_use: '已停用',
    max_tokens: '已达长度上限',
    fallback: '回退处理',
    error: '出错',
  }
  return reason ? map[reason] || reason : ''
}
