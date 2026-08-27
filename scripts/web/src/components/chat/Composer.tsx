import { useRef, useState } from 'react'
import { Paperclip, PaperPlaneRight, StopCircle } from '@phosphor-icons/react'
import { useChatStore } from '@/state/useChatStore'
import { cn } from '@/lib/cn'
import { Button } from '@/components/ui/Button'
import { IconButton } from '@/components/ui/IconButton'
import { Switch } from '@/components/ui/Switch'
import { Tooltip } from '@/components/ui/Tooltip'

export function Composer({ onSubmit }: { onSubmit: (query: string) => void }) {
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const deepThink = useChatStore((s) => s.deepThink)
  const stop = useChatStore((s) => s.stop)
  const setDeepThink = useChatStore((s) => s.setDeepThink)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const autoGrow = () => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }

  const submit = () => {
    const q = text.trim()
    if (!q || isStreaming) return
    setText('')
    setFileName(null)
    if (taRef.current) taRef.current.style.height = 'auto'
    onSubmit(q)
  }

  return (
    <div className="border-t border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="mx-auto max-w-3xl">
        <div className="rounded-2xl border border-zinc-300 bg-white p-2 shadow-card transition-colors focus-within:border-primary-400 dark:border-zinc-700 dark:bg-zinc-900">
          <textarea
            ref={taRef}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              autoGrow()
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit()
              }
            }}
            placeholder="向智能体提问…（Enter 发送，Shift+Enter 换行）"
            rows={1}
            className="max-h-[180px] min-h-[44px] w-full resize-none bg-transparent px-2.5 py-2 text-[14.5px] leading-relaxed text-zinc-800 outline-none placeholder:text-zinc-400 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          />

          {fileName && (
            <div className="mx-2.5 mb-1 flex items-center gap-2 rounded-lg bg-zinc-50 px-2.5 py-1.5 text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              <span className="truncate">{fileName}</span>
              <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
                待接入
              </span>
            </div>
          )}

          <div className="flex items-center gap-1.5 px-1">
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.doc,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) setFileName(f.name)
                e.target.value = ''
              }}
            />
            <Tooltip content="上传文档给知识库（待接入）">
              <IconButton label="上传文档" onClick={() => fileRef.current?.click()}>
                <Paperclip size={17} />
              </IconButton>
            </Tooltip>

            <Switch
              className="ml-1"
              checked={deepThink}
              onCheckedChange={setDeepThink}
              label="深度思考"
            />

            <div className="ml-auto flex items-center gap-2">
              {isStreaming ? (
                <Button variant="outline" onClick={stop}>
                  <StopCircle size={16} weight="fill" />
                  停止
                </Button>
              ) : (
                <Button onClick={submit} disabled={!text.trim()}>
                  <PaperPlaneRight size={16} weight="fill" />
                  发送
                </Button>
              )}
            </div>
          </div>
        </div>
        <p className="mt-1.5 px-1 text-[11px] text-zinc-400 dark:text-zinc-500">
          AI 生成内容仅供参考，不构成任何投资建议。
        </p>
      </div>
    </div>
  )
}
