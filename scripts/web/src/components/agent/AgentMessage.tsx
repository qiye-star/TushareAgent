import { useEffect, useState } from 'react'
import { ArrowsClockwise, BookOpen, Check, Copy, Robot } from '@phosphor-icons/react'
import { copyText } from '@/lib/api'
import type { ChatTurn } from '@/lib/types'
import { IconButton } from '@/components/ui/IconButton'
import { SideSheet } from '@/components/ui/SideSheet'
import { Tooltip } from '@/components/ui/Tooltip'
import { AnswerBlock } from './AnswerBlock'
import { CitationList } from './CitationList'
import { ThinkingTrace } from './ThinkingTrace'

type Props = {
  turn: ChatTurn
  canRegenerate?: boolean
  onRegenerate?: (query: string) => void
}

export function AgentMessage({ turn, canRegenerate, onRegenerate }: Props) {
  const [copied, setCopied] = useState(false)
  const [refOpen, setRefOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState<number | null>(null)

  const handleCopy = async () => {
    await copyText(turn.answer)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  // 点击正文内联 [n] 标记：打开引用抽屉并高亮第 n 条来源
  const jumpToRef = (n: number) => {
    setActiveIndex(n)
    setRefOpen(true)
  }

  const openRefs = () => {
    setActiveIndex(null)
    setRefOpen(true)
  }

  // 抽屉打开且指定了高亮来源时，把该来源滚动到可视区域（抽屉内）
  useEffect(() => {
    if (refOpen && activeIndex != null) {
      requestAnimationFrame(() => {
        document
          .getElementById(`cit-${activeIndex}`)
          ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      })
    }
  }, [refOpen, activeIndex])

  return (
    <div className="space-y-3">
      {/* agent 身份头部（机器人图标移到消息最上方） */}
      <div className="flex items-center gap-2">
        <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-primary-100 text-primary-700 dark:bg-primary-500/20 dark:text-primary-300">
          <Robot size={13} weight="fill" />
        </div>
        <span className="text-[12px] font-medium text-zinc-500 dark:text-zinc-400">AI 助手</span>
      </div>

      <ThinkingTrace steps={turn.steps} thinking={turn.thinking} streaming={turn.status === 'streaming'} />

      <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-soft dark:border-zinc-800 dark:bg-zinc-900">
        <AnswerBlock
          answer={turn.answer}
          citations={turn.citations}
          streaming={turn.status === 'streaming'}
          onJumpToRef={jumpToRef}
        />

        {/* message actions */}
        <div className="mt-3 flex items-center gap-1 border-t border-zinc-100 pt-2.5 dark:border-zinc-800">
          <Tooltip content={copied ? '已复制' : '复制回答'}>
            <IconButton label={copied ? '已复制' : '复制回答'} onClick={handleCopy}>
              {copied ? <Check size={16} weight="bold" /> : <Copy size={16} />}
            </IconButton>
          </Tooltip>
          {canRegenerate && (
            <Tooltip content="重新生成">
              <IconButton
                label="重新生成"
                onClick={() => onRegenerate?.(turn.query)}
                disabled={turn.status === 'streaming'}
              >
                <ArrowsClockwise size={16} />
              </IconButton>
            </Tooltip>
          )}
          {turn.stoppedReason === 'fallback' && (
            <span className="ml-auto text-[11px] text-zinc-400 dark:text-zinc-500">
              · 已回退处理
            </span>
          )}
        </div>
      </div>

      {/* 最下方：引用来源链接，点击打开右侧抽屉 */}
      {turn.sources.length > 0 && (
        <button
          type="button"
          onClick={openRefs}
          className="group inline-flex items-center gap-1.5 text-[13px] font-medium text-primary-600 underline-offset-2 transition-colors hover:text-primary-500 hover:underline dark:text-primary-400 dark:hover:text-primary-300"
        >
          <BookOpen size={14} className="shrink-0 transition-transform group-hover:-translate-y-0.5" />
          查看引用来源 ({turn.sources.length})
        </button>
      )}

      <SideSheet
        open={refOpen}
        onOpenChange={setRefOpen}
        title="引用来源"
        description={turn.query ? `针对「${turn.query}」` : undefined}
      >
        <CitationList
          sources={turn.sources}
          activeIndex={activeIndex}
          hideHeader
          query={turn.query}
          createdAt={turn.createdAt}
        />
      </SideSheet>
    </div>
  )
}
