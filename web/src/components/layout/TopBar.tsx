import { List, Moon, Sun, GitBranch, Prohibit } from '@phosphor-icons/react'
import type { Theme } from '@/lib/storage'
import { cn } from '@/lib/cn'
import { Badge } from '@/components/ui/Badge'
import { IconButton } from '@/components/ui/IconButton'
import { Tooltip } from '@/components/ui/Tooltip'

type Status = 'idle' | 'streaming' | 'error'

type Props = {
  title: string
  status: Status
  theme: Theme
  onToggleTheme: () => void
  onToggleSidebar: () => void
  onReset: () => void
  onClearContext: () => void
}

export function TopBar({
  title,
  status,
  theme,
  onToggleTheme,
  onToggleSidebar,
  onReset,
  onClearContext,
}: Props) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-zinc-200 bg-white/80 px-4 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
      <IconButton label="切换侧边栏" onClick={onToggleSidebar}>
        <List size={18} />
      </IconButton>

      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-semibold text-zinc-800 dark:text-zinc-100">
          {title}
        </span>
        <Badge tone={status === 'error' ? 'error' : status === 'streaming' ? 'primary' : 'neutral'}>
          {status === 'streaming' ? '生成中' : status === 'error' ? '出错' : '空闲'}
        </Badge>
      </div>

      <div className="ml-auto flex items-center gap-1">
        <Tooltip content="重启会话（新会话）">
          <IconButton label="新会话" onClick={onReset}>
            <GitBranch size={17} />
          </IconButton>
        </Tooltip>
        <Tooltip content="清空上下文（删除当前会话）">
          <IconButton label="清空上下文" onClick={onClearContext}>
            <Prohibit size={17} />
          </IconButton>
        </Tooltip>
        <div
          className={cn(
            'mx-1 h-6 w-px bg-zinc-200 dark:bg-zinc-800',
          )}
        />
        <Tooltip content={theme === 'dark' ? '切换浅色模式' : '切换深色模式'}>
          <IconButton label="切换主题" onClick={onToggleTheme}>
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </IconButton>
        </Tooltip>
      </div>
    </header>
  )
}
