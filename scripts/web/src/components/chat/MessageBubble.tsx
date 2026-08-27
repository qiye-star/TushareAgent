import { WarningCircle } from '@phosphor-icons/react'
import type { ChatTurn } from '@/lib/types'
import { AgentMessage } from '@/components/agent/AgentMessage'

function ErrorBubble({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[13px] text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
      <WarningCircle size={16} className="mt-0.5 shrink-0" weight="fill" />
      <span className="min-w-0 leading-relaxed">{message}</span>
    </div>
  )
}

export function MessageBubble({
  turn,
  canRegenerate,
  onRegenerate,
}: {
  turn: ChatTurn
  canRegenerate?: boolean
  onRegenerate?: (query: string) => void
}) {
  return (
    <div className="space-y-4">
      {/* user */}
      <div className="flex justify-end">
        <div className="max-w-[78%] rounded-2xl rounded-tr-md bg-primary-600 px-4 py-2.5 text-[14.5px] leading-relaxed text-white shadow-soft">
          <p className="whitespace-pre-wrap break-words">{turn.query}</p>
        </div>
      </div>

      {/* agent */}
      {turn.error ? (
        <ErrorBubble message={turn.error} />
      ) : (
        <AgentMessage turn={turn} canRegenerate={canRegenerate} onRegenerate={onRegenerate} />
      )}
    </div>
  )
}
