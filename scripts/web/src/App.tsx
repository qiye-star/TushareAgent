import { useCallback, useEffect, useMemo, useState } from 'react'
import { TooltipProvider, Tooltip } from '@/components/ui/Tooltip'
import { IconButton } from '@/components/ui/IconButton'
import { useTheme } from '@/hooks/useTheme'
import { useQuickReport } from '@/hooks/useQuickReport'
import { useSessions } from '@/hooks/useSessions'
import { useMcpStatus } from '@/hooks/useMcpStatus'
import { useMcpSources } from '@/hooks/useMcpSources'
import { useChatStore } from '@/state/useChatStore'
import { getSession, getSessionTurns } from '@/lib/api'
import type { SkillRow } from '@/lib/skills'
import { AppShell } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/layout/Sidebar'
import { TopBar } from '@/components/layout/TopBar'
import { ConfigPanel } from '@/components/layout/ConfigPanel'
import { MessageList } from '@/components/chat/MessageList'
import { Composer } from '@/components/chat/Composer'
import { QuickReportView } from '@/components/report/QuickReportView'
import { SettingsView } from '@/components/settings/SettingsView'
import { SkillsView } from '@/components/skills/SkillsView'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { GearSix } from '@phosphor-icons/react'

export type MainView = 'chat' | 'report' | 'skills' | 'settings'

export default function App() {
  const { theme, toggle: toggleTheme } = useTheme()
  const sessions = useSessions()
  const quickReport = useQuickReport()
  const mcpStatus = useMcpStatus()
  const mcpSources = useMcpSources()

  const sessionId = useChatStore((s) => s.sessionId)
  const turns = useChatStore((s) => s.turns)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const send = useChatStore((s) => s.send)
  const stop = useChatStore((s) => s.stop)
  const resetLocal = useChatStore((s) => s.resetLocal)
  const loadHistory = useChatStore((s) => s.loadHistory)
  const loadTurns = useChatStore((s) => s.loadTurns)
  const setPendingSkill = useChatStore((s) => s.setPendingSkill)

  const [activeId, setActiveId] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [view, setView] = useState<MainView>('chat')
  const [pendingAction, setPendingAction] = useState<'reset' | 'clear' | null>(null)

  const lastTurn = turns[turns.length - 1]

  const handleSend = useCallback(
    async (query: string) => {
      const id = await send(query)
      setActiveId(id || null)
      await sessions.refresh()
    },
    [send, sessions],
  )

  // 技能页「快速使用」：把技能挂到输入框（强制本轮使用），并切回对话页
  const handleQuickUseSkill = useCallback(
    (skill: SkillRow) => {
      setPendingSkill({ id: skill.id, name: skill.name })
      setView('chat')
    },
    [setPendingSkill],
  )

  const handleNew = useCallback(() => {
    resetLocal()
    setActiveId(null)
  }, [resetLocal])

  const handleSelect = useCallback(
    async (id: string) => {
      setActiveId(id)
      try {
        // 优先用每轮的完整载荷（thinking/工具/ReAct/引用）还原；旧会话无此表则回退扁平日志。
        const blobs = await getSessionTurns(id)
        if (blobs.length) {
          loadTurns(id, blobs)
        } else {
          const msgs = await getSession(id)
          loadHistory(id, msgs.map((m) => ({ role: m.role, content: m.content, is_error: m.is_error })))
        }
      } catch {
        loadHistory(id, [])
      }
    },
    [loadTurns, loadHistory],
  )

  const handleDelete = useCallback(
    async (id: string) => {
      await sessions.remove(id)
      if (id === activeId) {
        resetLocal()
        setActiveId(null)
      }
    },
    [sessions, activeId, resetLocal],
  )

  const handleClearContext = useCallback(async () => {
    if (activeId) await sessions.remove(activeId)
    resetLocal()
    setActiveId(null)
  }, [activeId, sessions, resetLocal])

  const handleRegenerate = useCallback(
    async (query: string) => {
      setActiveId((await send(query)) || null)
      await sessions.refresh()
    },
    [send, sessions],
  )

  // Suggestion buttons in the empty state dispatch a custom event.
  useEffect(() => {
    const onSuggest = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail
      if (detail) void handleSend(detail)
    }
    window.addEventListener('suggest', onSuggest)
    return () => window.removeEventListener('suggest', onSuggest)
  }, [handleSend])

  const status =
    isStreaming ? 'streaming' : lastTurn?.status === 'error' ? 'error' : 'idle'

  const title = activeId
    ? sessions.titles[activeId] || '会话'
    : isStreaming || turns.length
      ? sessions.titles[(sessionId as string)] || '会话'
      : '新会话'

  const kbInfo = useMemo(() => {
    const ragChunks = lastTurn?.metadata?.rag_chunks ?? null
    const sources = (lastTurn?.sources ?? []).map((s) => s.title)
    return { ragChunks, sources }
  }, [lastTurn])

  return (
    <TooltipProvider>
      <AppShell
        sidebarCollapsed={sidebarCollapsed}
        configOpen={configOpen}
        onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
        onToggleConfig={() => setConfigOpen((v) => !v)}
        sidebar={
          <Sidebar
            collapsed={sidebarCollapsed}
            view={view}
            onViewChange={setView}
            sessions={sessions.sessions}
            titles={sessions.titles}
            activeId={activeId}
            histories={quickReport.histories}
            activeReportDate={quickReport.activeDate}
            onSelectHistory={(day) => void quickReport.selectHistory(day)}
            onNew={handleNew}
            onSelect={(id) => void handleSelect(id)}
            onDelete={(id) => void handleDelete(id)}
            onRename={(id, t) => sessions.rename(id, t)}
            onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
          />
        }
        config={
          <ConfigPanel
            open={configOpen}
            onToggle={() => setConfigOpen((v) => !v)}
            kbInfo={kbInfo}
          />
        }
        main={
          view === 'report' ? (
            <>
              <TopBar
                title="AI算力产业链 · 高频跟踪快报"
                status="idle"
                theme={theme}
                onToggleTheme={toggleTheme}
                onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
                onReset={() => {}}
                onClearContext={() => {}}
              />
              <QuickReportView
                report={quickReport.report}
                activeDate={quickReport.activeDate}
                loading={quickReport.loading}
                generating={quickReport.generating}
                error={quickReport.error}
                onRefresh={() => void quickReport.refresh()}
                onRegenerate={() => void quickReport.regenerate()}
                onBackToLatest={() => void quickReport.refresh()}
              />
            </>
          ) : view === 'skills' ? (
            <>
              <TopBar
                title="投研技能库"
                status="idle"
                theme={theme}
                onToggleTheme={toggleTheme}
                onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
                onReset={() => {}}
                onClearContext={() => {}}
              />
              <SkillsView onQuickUse={handleQuickUseSkill} />
            </>
          ) : view === 'settings' ? (
            <>
              <TopBar
                title="MCP 数据源设置"
                status="idle"
                theme={theme}
                onToggleTheme={toggleTheme}
                onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
                onReset={() => {}}
                onClearContext={() => {}}
              />
              <SettingsView
                status={mcpStatus.status}
                loading={mcpStatus.loading}
                toggling={mcpStatus.toggling}
                error={mcpStatus.error}
                onToggle={(v) => void mcpStatus.toggle(v)}
                onRefresh={() => void mcpStatus.refresh()}
                sources={mcpSources.sources}
                gatewayUrl={mcpSources.gatewayUrl}
                gatewayReachable={mcpSources.reachable}
                configProblems={mcpSources.configProblems}
                sourcesLoading={mcpSources.loading}
                togglingSourceId={mcpSources.togglingId}
                sourcesError={mcpSources.error}
                onToggleSource={(id, v) => void mcpSources.toggle(id, v)}
                onRefreshSources={() => void mcpSources.refresh()}
              />
            </>
          ) : (
            <>
              <TopBar
                title={title}
                status={status}
                theme={theme}
                onToggleTheme={toggleTheme}
                onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
                onReset={() => setPendingAction('reset')}
                onClearContext={() => setPendingAction('clear')}
              />
              <MessageList onRegenerate={(q) => void handleRegenerate(q)} />
              <Composer onSubmit={(q) => void handleSend(q)} />
              {/* Mobile config floating toggle */}
              <div className="fixed bottom-5 right-5 z-20 hidden max-md:block">
                <Tooltip content="配置">
                  <IconButton
                    label="配置"
                    onClick={() => setConfigOpen(true)}
                    className="h-11 w-11 rounded-full bg-primary-600 text-white shadow-lg hover:bg-primary-500"
                  >
                    <GearSix size={20} weight="bold" />
                  </IconButton>
                </Tooltip>
              </div>
            </>
          )
        }
      />

      <ConfirmDialog
        open={pendingAction !== null}
        onOpenChange={() => setPendingAction(null)}
        title={pendingAction === 'clear' ? '清空当前会话？' : '重启会话？'}
        description={
          pendingAction === 'clear'
            ? '将删除当前会话及其全部对话记录，此操作不可撤销。'
            : '将清除当前对话视图并开始一个新会话（历史会话仍保留在侧栏）。'
        }
        confirmLabel={pendingAction === 'clear' ? '清空' : '重启'}
        danger={pendingAction === 'clear'}
        onConfirm={() => {
          if (pendingAction === 'reset') handleNew()
          else if (pendingAction === 'clear') void handleClearContext()
          setPendingAction(null)
        }}
      />
    </TooltipProvider>
  )
}
