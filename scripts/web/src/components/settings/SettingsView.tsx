import { Plugs, PlugsConnected, Warning, CircleNotch, Stack } from '@phosphor-icons/react'
import { Badge } from '@/components/ui/Badge'
import { Switch } from '@/components/ui/Switch'
import type { McpSource, McpStatus } from '@/lib/mcp'

/**
 * 设置视图（主区，与「聊天/快报」同级）：MCP 数据源全局开关 + （网关模式下）按源明细开关。
 * 状态由 App 层 useMcpStatus/useMcpSources 提供；关闭立即对所有新会话生效（不影响正在进行的对话）。
 */
type Props = {
  status: McpStatus | null
  loading: boolean
  toggling: boolean
  error: string | null
  onToggle: (enabled: boolean) => void
  onRefresh: () => void
  sources: McpSource[]
  gatewayUrl: string
  gatewayReachable: boolean
  configProblems: string[]
  sourcesLoading: boolean
  togglingSourceId: string | null
  sourcesError: string | null
  onToggleSource: (id: string, enabled: boolean) => void
  onRefreshSources: () => void
}

export function SettingsView({
  status,
  loading,
  toggling,
  error,
  onToggle,
  onRefresh,
  sources,
  gatewayUrl,
  gatewayReachable,
  configProblems,
  sourcesLoading,
  togglingSourceId,
  sourcesError,
  onToggleSource,
  onRefreshSources,
}: Props) {
  const enabled = status?.enabled ?? true

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 md:px-8">
        <div className="mx-auto max-w-2xl space-y-4">
          <section className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
            <div className="flex items-center gap-2">
              {enabled ? (
                <PlugsConnected size={16} className="text-primary-600 dark:text-primary-400" weight="duotone" />
              ) : (
                <Plugs size={16} className="text-zinc-400" weight="duotone" />
              )}
              <span className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
                MCP 数据源
              </span>
              {loading ? (
                <Badge tone="neutral">
                  <CircleNotch size={11} className="animate-spin" />
                  加载中
                </Badge>
              ) : (
                <>
                  <Badge tone={enabled ? 'success' : 'neutral'}>{enabled ? '已启用' : '已停用'}</Badge>
                  <Badge tone={status?.connected ? 'success' : 'neutral'}>
                    {status?.connected ? '已连接' : '未连接'}
                  </Badge>
                  {status?.tool_count != null && <Badge tone="neutral">{status.tool_count} 个工具</Badge>}
                </>
              )}
              <button
                type="button"
                onClick={onRefresh}
                className="ml-auto text-[11.5px] text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
              >
                刷新
              </button>
            </div>

            <div className="mt-4 flex items-center justify-between gap-4 rounded-lg bg-zinc-50 px-3 py-2.5 dark:bg-zinc-900">
              <div className="min-w-0">
                <div className="text-[13px] font-medium text-zinc-700 dark:text-zinc-200">
                  启用 MCP 取数工具
                </div>
                <div className="mt-0.5 text-[11.5px] leading-relaxed text-zinc-400 dark:text-zinc-500">
                  总闸：本服务是否使用取数工具。关闭 → 对话退化为纯 LLM 问答、不连网关；影响所有会话、
                  改动立即生效、无需重启，正在进行中的对话不受影响。要单独开关某个数据源（Tushare / 万得）
                  请用下面的「数据源明细」。
                </div>
              </div>
              <Switch checked={enabled} onCheckedChange={onToggle} disabled={loading || toggling} />
            </div>

            {error && (
              <div className="mt-3 flex items-center gap-1.5 text-[11.5px] text-rose-600 dark:text-rose-400">
                <Warning size={13} />
                {error}
              </div>
            )}
          </section>

          {/* 数据源明细：恒定渲染。网关没起来/没配好时显示诊断，而不是把整块藏起来。 */}
          <section className="rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
            <div className="flex items-center gap-2">
              <Stack size={16} className="text-primary-600 dark:text-primary-400" weight="duotone" />
              <span className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
                数据源明细（MCP 网关）
              </span>
              {sourcesLoading ? (
                <Badge tone="neutral">
                  <CircleNotch size={11} className="animate-spin" />
                  加载中
                </Badge>
              ) : (
                <Badge tone={gatewayReachable ? 'success' : 'error'}>
                  {gatewayReachable ? '网关在线' : '网关不可达'}
                </Badge>
              )}
              <button
                type="button"
                onClick={onRefreshSources}
                className="ml-auto text-[11.5px] text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
              >
                刷新
              </button>
            </div>
            <div className="mt-0.5 text-[11.5px] leading-relaxed text-zinc-400 dark:text-zinc-500">
              Tushare / 万得等上游源由独立部署的 MCP 网关（<code className="font-mono">{gatewayUrl || '未配置'}</code>
              ）持有并聚合，可按源单独开关；关闭某个源后 agent 立即不再看到它的工具。
            </div>

            {!gatewayReachable && !sourcesLoading && (
              <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-[12px] leading-relaxed text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
                <div className="flex items-center gap-1.5 font-medium">
                  <Warning size={13} />
                  连不上 MCP 网关
                </div>
                <div className="mt-1">
                  请先启动网关（本地：<code className="font-mono">scripts/dev_up.ps1</code> 或
                  <code className="font-mono"> uv run uvicorn mcp_gateway.app:app --port 8766</code>；
                  容器：<code className="font-mono">docker compose up -d mcp-gateway</code>）。
                  网关未启动时对话不会取数，但仍可作为纯 LLM 使用。
                </div>
                {sourcesError && <div className="mt-1 font-mono text-[11px] opacity-80">{sourcesError}</div>}
              </div>
            )}

            {configProblems.length > 0 && (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-[12px] leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
                <div className="flex items-center gap-1.5 font-medium">
                  <Warning size={13} />
                  网关配置问题
                </div>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {configProblems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="mt-3 space-y-1">
              {gatewayReachable && sources.length === 0 && !sourcesLoading && (
                <div className="rounded-lg bg-zinc-50 px-3 py-4 text-center text-[12.5px] text-zinc-400 dark:bg-zinc-900 dark:text-zinc-500">
                  网关在线但没有注册任何数据源（见上方配置问题）
                </div>
              )}
              {sources.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center justify-between gap-4 rounded-lg bg-zinc-50 px-3 py-2.5 dark:bg-zinc-900"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5 text-[13px] font-medium text-zinc-700 dark:text-zinc-200">
                      {s.display_name}
                      <span className="font-mono text-[11px] text-zinc-400 dark:text-zinc-500">{s.id}</span>
                      <Badge tone={s.connected ? 'success' : 'neutral'}>
                        {s.connected ? '已连接' : '未连接'}
                      </Badge>
                      {s.tool_count != null && <Badge tone="neutral">{s.tool_count} 个工具</Badge>}
                    </div>
                  </div>
                  <Switch
                    checked={s.enabled}
                    onCheckedChange={(v) => onToggleSource(s.id, v)}
                    disabled={sourcesLoading || togglingSourceId === s.id}
                  />
                </div>
              ))}
            </div>

            {sourcesError && gatewayReachable && (
              <div className="mt-3 flex items-center gap-1.5 text-[11.5px] text-rose-600 dark:text-rose-400">
                <Warning size={13} />
                {sourcesError}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
