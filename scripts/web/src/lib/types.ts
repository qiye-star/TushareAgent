// Backend contract types, mirroring demomcp/interfaces/types.py,
// demomcp/graph/nodes.py (_structured), and demomcp/entry/web.py (SSE events).

export type StoppedReason =
  | 'end_turn'
  | 'tool_use'
  | 'max_tokens'
  | 'fallback'
  | 'error'
  | (string & {})

export type ToolResult = {
  content: string
  is_error?: boolean
}

export type TurnStatus = 'streaming' | 'done' | 'error' | 'stopped'

// ---- structured output (comes on the `done` event) ----
export type SourceCard =
  | {
      type: 'rag'
      title: string
      company: string | null
      year: number | null
      page: string | null
      section: string
      inline: string
      /** 该条检索内容摘要（区分同来源重复块）。 */
      excerpt?: string
    }
  | {
      type: 'tool'
      title: string
      data: string
      params?: Record<string, unknown>
    }

export type CitationCard =
  | {
      ref_index: number
      type: 'rag'
      title: string
      page: string | null
      section: string
      company: string | null
      year: number | null
      inline: string
    }
  | {
      ref_index: number
      type: 'tool'
      title: string
    }

export type Claim = {
  text: string
  source: string
}

export type StructuredMeta = {
  request: Record<string, unknown>
  validation: { errors: string[]; normalized: boolean }
  tool_results: number
  rag_chunks: number
}

export type StructuredOutput = {
  answer: string
  intent: string | null
  strategy: 'structural' | 'factual' | 'auto' | (string & {})
  sources: SourceCard[]
  citations: CitationCard[]
  claims: Claim[]
  metadata: StructuredMeta | null
}

// ---- ReAct trace / process steps ----
export type StepKind =
  | 'intent'
  | 'plan'
  | 'rewrite'
  | 'retrieval'
  | 'funnel'
  | 'params'
  | 'validation'
  | 'aggregate'
  | 'stage'
  | 'tool_call'
  | 'tool_result'
  | 'loop_turn'

/** 一轮 agentic tool loop 的快照（tool_rag 条件自环的每一轮）。 */
export type LoopTurnStepData = {
  round: number
  status: 'continue' | 'stop' | 'max-reached'
  tools: string[]
  evidence: number
}

export type Step = {
  kind: StepKind
  /** Tool name for tool steps, otherwise a human label. */
  label: string
  data: any
}

export type ChatTurn = {
  id: string
  sessionId: string
  query: string
  status: TurnStatus
  /** Accumulated from `thinking` events (streamed). */
  thinking: string
  /** Accumulated from `text` events; authoritative value on `done.structured.answer`. */
  answer: string
  /** ReAct chain, ordered. */
  steps: Step[]
  sources: SourceCard[]
  citations: CitationCard[]
  claims: Claim[]
  metadata: StructuredMeta | null
  stoppedReason: StoppedReason | null
  usage: Record<string, unknown> | null
  error?: string
}

// ---- SSE wire messages ----
export type DonePayload = {
  stopped_reason: StoppedReason
  session_id: string
  usage: Record<string, unknown> | null
  structured: StructuredOutput | null
}

export type ErrorPayload = { message: string }

export type RawSse = { event: string; data: any }

// ---- persisted per-turn blob (GET /api/sessions/{id}/turns) ----
export type TurnBlob = {
  query: string
  thinking: string
  steps: { kind: string; data: any }[]
  answer: string
  sources: SourceCard[]
  citations: CitationCard[]
  claims: Claim[]
  metadata: StructuredMeta | null
  intent: string | null
  strategy: string | null
  stopped_reason: string | null
  usage: Record<string, unknown> | null
  error?: string | null
}

// ---- sessions API ----
export type SessionMeta = {
  session_id: string
  count: number
  created_at: string | null
}

export type SessionMessage = {
  role: 'user' | 'assistant' | 'tool' | 'error'
  content: string
  is_error: boolean
  created_at: string | null
}
