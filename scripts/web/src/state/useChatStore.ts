import { create } from 'zustand'
import { streamChat } from '@/lib/sse'
import {
  type ChatTurn,
  type StructuredOutput,
  type StoppedReason,
  type RawSse,
  type TurnBlob,
} from '@/lib/types'
import { cleanAnswer, stepOf } from '@/lib/format'

type Patch = Partial<ChatTurn>

type ChatState = {
  sessionId: string | null
  turns: ChatTurn[]
  isStreaming: boolean
  deepThink: boolean
  controller: AbortController | null

  setDeepThink: (v: boolean) => void
  setSessionId: (id: string) => void

  /** Begin a turn and stream a message; resolves with the session id used. */
  send: (query: string, opts?: { session_id?: string | null }) => Promise<string>
  stop: () => void

  resetLocal: () => void
  /** Delete the current session server-side + clear local state. */
  clearContext: () => void
  removeTurn: (id: string) => void
  removeTurnsFrom: (id: string) => void
  /** Replay a flat message log (role/content) into turn objects. */
  loadHistory: (sessionId: string, messages: { role: string; content: string; is_error?: boolean }[]) => void
  /** Replay persisted per-turn blobs with full thinking/tool/ReAct/structured data. */
  loadTurns: (sessionId: string, blobs: TurnBlob[]) => void
}

let uid = 0
const nextId = () => `t${Date.now().toString(36)}-${++uid}`

function patchTurn(turns: ChatTurn[], id: string, patch: Patch): ChatTurn[] {
  return turns.map((t) => (t.id === id ? { ...t, ...patch } : t))
}

export const useChatStore = create<ChatState>((set, get) => {
  const applyEvent = (turnId: string, evt: RawSse) => {
    const { event, data } = evt
    switch (event) {
      case 'thinking':
        set((s) => ({
          turns: patchTurn(s.turns, turnId, {
            thinking: (s.turns.find((t) => t.id === turnId)?.thinking ?? '') + data.text,
          }),
        }))
        break
      case 'text':
        set((s) => ({
          turns: patchTurn(s.turns, turnId, {
            answer: (s.turns.find((t) => t.id === turnId)?.answer ?? '') + data.text,
          }),
        }))
        break
      case 'process':
        set((s) => ({
          turns: patchTurn(s.turns, turnId, {
            steps: [...(s.turns.find((t) => t.id === turnId)?.steps ?? []), stepOf(data.kind, data)],
          }),
        }))
        break
      case 'tool_call':
        set((s) => ({
          turns: patchTurn(s.turns, turnId, {
            steps: [
              ...(s.turns.find((t) => t.id === turnId)?.steps ?? []),
              stepOf('tool_call', { name: data.name, input: data.input }),
            ],
          }),
        }))
        break
      case 'tool_result':
        set((s) => ({
          turns: patchTurn(s.turns, turnId, {
            steps: [
              ...(s.turns.find((t) => t.id === turnId)?.steps ?? []),
              stepOf('tool_result', { name: data.name, content: data.content, ok: data.ok }),
            ],
          }),
        }))
        break
      case 'done': {
        const structured: StructuredOutput | null = data.structured ?? null
        set((s) => {
          const current = s.turns.find((t) => t.id === turnId)
          return {
            sessionId: data.session_id || null,
            isStreaming: false,
            turns: patchTurn(s.turns, turnId, {
              status: 'done',
              sessionId: data.session_id,
              answer: cleanAnswer(structured?.answer ?? current?.answer ?? ''),
              thinking: current?.thinking ?? '',
              steps: current?.steps ?? [],
              sources: structured?.sources ?? [],
              citations: structured?.citations ?? [],
              claims: structured?.claims ?? [],
              metadata: structured?.metadata ?? null,
              stoppedReason: data.stopped_reason as StoppedReason,
              usage: data.usage ?? null,
            }),
          }
        })
        break
      }
      case 'error': {
        set((s) => ({
          isStreaming: false,
          turns: patchTurn(s.turns, turnId, {
            status: 'error',
            error: data.message ?? '服务异常',
          }),
        }))
        break
      }
      default:
        break
    }
  }

  return {
    sessionId: null,
    turns: [],
    isStreaming: false,
    deepThink: false,
    controller: null,

    setDeepThink: (v) => set({ deepThink: v }),

    setSessionId: (id) => set({ sessionId: id }),

    send: async (query, opts) => {
      const sessionId = get().sessionId
      const turnId = nextId()
      const controller = new AbortController()
      const baseSession = opts?.session_id ?? sessionId ?? null

      set((s) => ({
        controller,
        isStreaming: true,
        turns: [
          ...s.turns,
          {
            id: turnId,
            sessionId: baseSession ?? '',
            query,
            status: 'streaming',
            thinking: '',
            answer: '',
            steps: [],
            sources: [],
            citations: [],
            claims: [],
            metadata: null,
            stoppedReason: null,
            usage: null,
          },
        ],
      }))

      try {
        await streamChat(
          '/chat',
          {
            message: query,
            session_id: baseSession,
            model: get().deepThink ? 'deepseek-reasoner' : null,
          },
          (evt) => applyEvent(turnId, evt),
          controller.signal,
        )
        // If the stream ended without an explicit done/error (e.g. cancelled), finalize.
        const turn = get().turns.find((t) => t.id === turnId)
        if (turn && turn.status === 'streaming') {
          set((s) => ({
            isStreaming: false,
            turns: patchTurn(s.turns, turnId, { status: 'stopped' }),
          }))
        }
      } catch (err) {
        if (controller.signal.aborted) {
          set((s) => ({
            isStreaming: false,
            turns: patchTurn(s.turns, turnId, { status: 'stopped' }),
          }))
        } else {
          set((s) => ({
            isStreaming: false,
            turns: patchTurn(s.turns, turnId, {
              status: 'error',
              error: err instanceof Error ? err.message : '网络异常',
            }),
          }))
        }
      }

      return get().sessionId ?? baseSession ?? ''
    },

    stop: () => {
      get().controller?.abort()
      set({ isStreaming: false })
    },

    resetLocal: () =>
      set({ turns: [], sessionId: null, controller: null, isStreaming: false }),

    clearContext: () =>
      set({ turns: [], sessionId: null, controller: null, isStreaming: false }),

    removeTurn: (id) =>
      set((s) => ({ turns: s.turns.filter((t) => t.id !== id) })),

    removeTurnsFrom: (id) => {
      const idx = get().turns.findIndex((t) => t.id === id)
      if (idx === -1) return
      set((s) => ({ turns: s.turns.slice(0, idx) }))
    },

    loadHistory: (sessionId, messages) => {
      const turns: ChatTurn[] = []
      let current: ChatTurn | null = null
      for (const m of messages) {
        if (m.role === 'user') {
          current = {
            id: nextId(),
            sessionId,
            query: m.content,
            status: 'done',
            thinking: '',
            answer: '',
            steps: [],
            sources: [],
            citations: [],
            claims: [],
            metadata: null,
            stoppedReason: 'end_turn',
            usage: null,
          }
          turns.push(current)
        } else if (m.role === 'assistant' && current) {
          current.answer += cleanAnswer(m.content)
        } else if (m.role === 'error' && current) {
          current.status = 'error'
          current.error = m.content
        }
      }
      set({ sessionId, turns, isStreaming: false, controller: null })
    },

    loadTurns: (sessionId, blobs) => {
      const turns: ChatTurn[] = blobs.map((b) => {
        const isError = !!b.error || b.stopped_reason === 'error'
        return {
          id: nextId(),
          sessionId,
          query: b.query,
          status: isError ? 'error' : 'done',
          thinking: b.thinking ?? '',
          answer: cleanAnswer(b.answer ?? ''),
          steps: (b.steps ?? []).map((s) => stepOf(s.kind, s.data)),
          sources: b.sources ?? [],
          citations: b.citations ?? [],
          claims: b.claims ?? [],
          metadata: b.metadata ?? null,
          stoppedReason: b.stopped_reason as StoppedReason,
          usage: b.usage ?? null,
          error: b.error ?? undefined,
        }
      })
      // Keep the last session id so subsequent sends continue this session.
      set({ sessionId, turns, isStreaming: false, controller: null })
    },
  }
})
