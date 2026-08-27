import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Props = {
  sidebar: ReactNode
  main: ReactNode
  config: ReactNode
  sidebarCollapsed: boolean
  configOpen: boolean
  onToggleSidebar: () => void
  onToggleConfig: () => void
}

export function AppShell({
  sidebar,
  main,
  config,
  sidebarCollapsed,
  configOpen,
  onToggleSidebar,
  onToggleConfig,
}: Props) {
  return (
    <div className="flex h-[100dvh] overflow-hidden bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      {/* Left rail/drawer */}
      <div
        className={cn(
          'z-30 h-full',
          'md:relative md:z-auto md:shrink-0',
          'max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:transition-transform max-md:duration-200',
          sidebarCollapsed ? 'max-md:-translate-x-full' : 'max-md:translate-x-0',
        )}
      >
        {sidebar}
      </div>
      {!sidebarCollapsed && (
        <div
          className="fixed inset-0 z-20 bg-zinc-950/30 backdrop-blur-sm md:hidden"
          onClick={onToggleSidebar}
        />
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        {main}
      </main>

      {/* Right panel: inline on desktop, drawer on mobile */}
      <div
        className={cn(
          'z-30',
          'md:relative md:z-auto md:shrink-0',
          'max-md:fixed max-md:inset-y-0 max-md:right-0 max-md:transition-transform max-md:duration-200',
          configOpen ? 'max-md:translate-x-0' : 'max-md:translate-x-full md:translate-x-0',
        )}
      >
        {config}
      </div>
      {configOpen && (
        <div
          className="fixed inset-0 z-20 bg-zinc-950/20 md:hidden"
          onClick={onToggleConfig}
        />
      )}
    </div>
  )
}
