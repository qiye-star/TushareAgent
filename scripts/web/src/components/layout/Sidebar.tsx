import { useState } from 'react'
import {
  ChartLineUp,
  ChatText,
  DotsThree,
  PencilSimple,
  Plugs,
  Plus,
  SidebarSimple,
  Trash,
} from '@phosphor-icons/react'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/cn'
import type { SessionMeta } from '@/lib/types'
import type { MainView } from '@/App'
import type { QuickReportHistoryItem } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { DropdownMenu, MenuItem } from '@/components/ui/DropdownMenu'
import { Dialog } from '@/components/ui/Dialog'

type Props = {
  collapsed: boolean
  view: MainView
  onViewChange: (v: MainView) => void
  sessions: SessionMeta[]
  titles: Record<string, string>
  activeId: string | null
  histories: QuickReportHistoryItem[]
  activeReportDate: string | null
  onSelectHistory: (day: string) => void
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
  onToggleCollapse: () => void
}

export function Sidebar({
  collapsed,
  view,
  onViewChange,
  sessions,
  titles,
  activeId,
  histories,
  activeReportDate,
  onSelectHistory,
  onNew,
  onSelect,
  onDelete,
  onRename,
  onToggleCollapse,
}: Props) {
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null)

  if (collapsed) {
    return (
      <aside className="flex h-full w-14 shrink-0 flex-col items-center border-r border-zinc-200 bg-white py-3 dark:border-zinc-800 dark:bg-zinc-950">
        <button
          type="button"
          onClick={onToggleCollapse}
          title="展开侧边栏"
          className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
        >
          <SidebarSimple size={19} />
        </button>
        <button
          type="button"
          onClick={onNew}
          title="新建会话"
          className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-600 text-white hover:bg-primary-500"
        >
          <Plus size={18} weight="bold" />
        </button>
        <div className="mt-2 flex w-full flex-col items-center gap-1 px-2">
          <button
            type="button"
            title="聊天"
            onClick={() => onViewChange('chat')}
            className={cn(
              'flex h-9 w-full items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800',
              view === 'chat' && 'bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300',
            )}
          >
            <ChatText size={17} />
          </button>
          <button
            type="button"
            title="快报"
            onClick={() => onViewChange('report')}
            className={cn(
              'flex h-9 w-full items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800',
              view === 'report' && 'bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300',
            )}
          >
            <ChartLineUp size={17} />
          </button>
          <button
            type="button"
            title="设置"
            onClick={() => onViewChange('settings')}
            className={cn(
              'flex h-9 w-full items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800',
              view === 'settings' && 'bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300',
            )}
          >
            <Plugs size={17} />
          </button>
        </div>
        <div className="mt-4 w-full space-y-1 px-2">
          {sessions.slice(0, 6).map((s) => (
            <button
              key={s.session_id}
              type="button"
              title={titles[s.session_id] || ''}
              onClick={() => onSelect(s.session_id)}
              className={cn(
                'flex h-9 w-full items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800',
                s.session_id === activeId && 'bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300',
              )}
            >
              <ChatText size={17} />
            </button>
          ))}
        </div>
      </aside>
    )
  }

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3.5 dark:border-zinc-800">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
          title="收起侧边栏"
        >
          <SidebarSimple size={18} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold tracking-tight text-zinc-800 dark:text-zinc-100">
            Tushare · demo-mcp
          </div>
          <div className="text-[11px] text-zinc-400 dark:text-zinc-500">AI 金融数据助手</div>
        </div>
        <Button size="sm" variant="primary" onClick={onNew} className="shrink-0">
          <Plus size={15} weight="bold" />
          新建
        </Button>
      </div>

      {/* 聊天 / 快报 板块切换：上下排版（上方导航，下方跟随对应记录列表） */}
      <nav className="flex flex-col gap-0.5 px-2 pt-2.5">
        <button
          type="button"
          onClick={() => onViewChange('chat')}
          className={cn(
            'flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium transition-colors',
            view === 'chat'
              ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
              : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800',
          )}
        >
          <ChatText size={17} className={cn(view === 'chat' && 'text-primary-600 dark:text-primary-400')} />
          聊天
        </button>
        <button
          type="button"
          onClick={() => onViewChange('report')}
          className={cn(
            'flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium transition-colors',
            view === 'report'
              ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
              : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800',
          )}
        >
          <ChartLineUp size={17} className={cn(view === 'report' && 'text-primary-600 dark:text-primary-400')} />
          快报
        </button>
        <button
          type="button"
          onClick={() => onViewChange('settings')}
          className={cn(
            'flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] font-medium transition-colors',
            view === 'settings'
              ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
              : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800',
          )}
        >
          <Plugs size={17} className={cn(view === 'settings' && 'text-primary-600 dark:text-primary-400')} />
          设置
        </button>
      </nav>

      {view === 'settings' ? (
        <div className="flex-1" />
      ) : view === 'report' ? (
        <>
          <div className="flex items-center justify-between px-4 pb-1 pt-3">
            <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
              历史快报
            </span>
          </div>
          <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
            {histories.length === 0 && (
              <div className="px-2 py-6 text-center text-[12.5px] text-zinc-400 dark:text-zinc-500">
                暂无历史快报
              </div>
            )}
            {histories.map((h) => {
              const active = h.date === activeReportDate || (activeReportDate == null && h.date === 'latest')
              return (
                <button
                  key={h.date}
                  type="button"
                  onClick={() => onSelectHistory(h.date)}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition-colors',
                    active
                      ? 'bg-primary-50 dark:bg-primary-500/10'
                      : 'hover:bg-zinc-100 dark:hover:bg-zinc-800',
                  )}
                >
                  <ChartLineUp
                    size={15}
                    className={cn(
                      'shrink-0 text-zinc-400 dark:text-zinc-500',
                      active && 'text-primary-600 dark:text-primary-400',
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span
                      className={cn(
                        'block truncate text-[13.5px] font-medium',
                        active ? 'text-primary-700 dark:text-primary-300' : 'text-zinc-700 dark:text-zinc-200',
                      )}
                    >
                      {h.date}
                    </span>
                  </span>
                  {!h.status_ok && (
                    <span className="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
                      缺段
                    </span>
                  )}
                </button>
              )
            })}
          </nav>
        </>
      ) : (
        <>
          <div className="flex items-center justify-between px-4 pb-1 pt-3">
            <span className="text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
              会话
            </span>
          </div>

          <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
        {sessions.length === 0 && (
          <div className="px-2 py-6 text-center text-[12.5px] text-zinc-400 dark:text-zinc-500">
            暂无会话
          </div>
        )}
        {sessions.map((s) => {
          const active = s.session_id === activeId
          return (
            <div
              key={s.session_id}
              className={cn(
                'group flex items-center gap-2 rounded-lg px-2.5 py-2 transition-colors',
                active
                  ? 'bg-primary-50 dark:bg-primary-500/10'
                  : 'hover:bg-zinc-100 dark:hover:bg-zinc-800',
              )}
            >
              <button
                type="button"
                onClick={() => onSelect(s.session_id)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <ChatText
                  size={16}
                  className={cn(
                    'shrink-0 text-zinc-400 dark:text-zinc-500',
                    active && 'text-primary-600 dark:text-primary-400',
                  )}
                />
                <span className="min-w-0 flex-1">
                  <span
                    className={cn(
                      'block truncate text-[13.5px] font-medium',
                      active
                        ? 'text-primary-700 dark:text-primary-300'
                        : 'text-zinc-700 dark:text-zinc-200',
                    )}
                  >
                    {titles[s.session_id] || '新会话'}
                  </span>
                  <span className="block text-[11px] text-zinc-400 dark:text-zinc-500">
                    {s.count} 条 · {formatTime(s.created_at)}
                  </span>
                </span>
              </button>
              <DropdownMenu
                trigger={
                  <button
                    type="button"
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-zinc-400 opacity-0 transition-opacity hover:bg-zinc-200 hover:text-zinc-600 group-hover:opacity-100 dark:hover:bg-zinc-700"
                  >
                    <DotsThree size={16} weight="bold" />
                  </button>
                }
              >
                <MenuItem
                  onSelect={() => setRenaming({ id: s.session_id, value: titles[s.session_id] || '' })}
                >
                  <PencilSimple size={14} className="mr-1.5 inline" />
                  重命名
                </MenuItem>
                <MenuItem destructive onSelect={() => onDelete(s.session_id)}>
                  <Trash size={14} className="mr-1.5 inline" />
                  删除
                </MenuItem>
              </DropdownMenu>
            </div>
          )
        })}
          </nav>
        </>
      )}

      <div className="border-t border-zinc-200 px-3 py-2.5 dark:border-zinc-800">
        <button
          type="button"
          onClick={onToggleCollapse}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:text-zinc-400"
        >
          <SidebarSimple size={17} />
          收起侧边栏
        </button>
      </div>

      <Dialog
        open={!!renaming}
        onOpenChange={() => setRenaming(null)}
        title="重命名会话"
        description="重命名为本地保存，不影响服务端。"
      >
        <input
          value={renaming?.value ?? ''}
          onChange={(e) => setRenaming((r) => (r ? { ...r, value: e.target.value } : r))}
          className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-500/20 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          placeholder="输入会话名称"
          autoFocus
        />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRenaming(null)}>
            取消
          </Button>
          <Button
            onClick={() => {
              if (renaming) onRename(renaming.id, renaming.value.trim())
              setRenaming(null)
            }}
          >
            保存
          </Button>
        </div>
      </Dialog>
    </aside>
  )
}
