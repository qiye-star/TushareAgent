import { useState } from 'react'
import {
  CloudArrowUp,
  Database,
  GearSix,
  Info,
  Slideshow,
  X,
} from '@phosphor-icons/react'
import { getToolSwitches, setToolSwitch } from '@/lib/storage'
import { cn } from '@/lib/cn'
import { Switch } from '@/components/ui/Switch'
import { Tooltip } from '@/components/ui/Tooltip'
import { Badge } from '@/components/ui/Badge'

const TOOLS: { id: string; name: string; desc: string }[] = [
  { id: 'stock_realtime', name: '实时行情', desc: '实时股价与买卖盘' },
  { id: 'stock_daily', name: '日线行情', desc: '历史日线 K 线数据' },
  { id: 'stock_daily_basic', name: '每日指标', desc: '市盈率/换手率等指标' },
  { id: 'stock_financial', name: '财务数据', desc: '利润表/资产负债/现金流' },
  { id: 'stock_compare', name: '财务对比', desc: '多标的财务指标对比' },
  { id: 'rag_query', name: '财报检索', desc: '年报知识库混合检索' },
]

type Props = {
  open: boolean
  onToggle: () => void
  kbInfo: { ragChunks: number | null; sources: string[] }
}

export function ConfigPanel({ open, onToggle, kbInfo }: Props) {
  const [switches, setSwitches] = useState(getToolSwitches)

  const toggleTool = (id: string, on: boolean) => {
    setSwitches((s) => ({ ...s, [id]: on }))
    setToolSwitch(id, on)
  }

  const enabledCount = TOOLS.filter((t) => switches[t.id] !== false).length

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={onToggle}
          title="打开配置面板"
          className="flex h-9 items-center gap-1 self-center rounded-lg border border-zinc-200 bg-white px-2 text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400"
        >
          <GearSix size={16} />
        </button>
      )}

      {open && (
        <aside className="flex h-full w-80 shrink-0 flex-col border-l border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
          <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3.5 dark:border-zinc-800">
            <GearSix size={17} className="text-zinc-500" />
            <span className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
              配置
            </span>
            <button
              type="button"
              onClick={onToggle}
              className="ml-auto flex h-7 w-7 items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:hover:bg-zinc-800"
              title="收起配置面板"
            >
              <X size={16} />
            </button>
          </div>

          <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
            {/* Available tools */}
            <section>
              <div className="mb-2.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
                <Slideshow size={12} />
                可用工具
              </div>
              <div className="flex items-center gap-1.5 text-[11.5px] text-zinc-400 dark:text-zinc-500">
                <Info size={12} className="shrink-0" />
                已启用 <span className="font-medium text-primary-600 dark:text-primary-400">{enabledCount}</span> / {TOOLS.length} 个 · 界面状态
              </div>
              <div className="mt-2 space-y-1">
                {TOOLS.map((t) => {
                  const on = switches[t.id] !== false
                  return (
                    <div
                      key={t.id}
                      className="flex items-center gap-3 rounded-lg px-2.5 py-2 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-medium text-zinc-700 dark:text-zinc-200">
                          {t.name}
                        </div>
                        <div className="truncate text-[11px] text-zinc-400 dark:text-zinc-500">
                          {t.desc}
                        </div>
                      </div>
                      <Tooltip content="界面开关 · 待接入">
                        <div>
                          <Switch checked={on} onCheckedChange={(v) => toggleTool(t.id, v)} />
                        </div>
                      </Tooltip>
                    </div>
                  )
                })}
              </div>
            </section>

            {/* Knowledge base */}
            <section>
              <div className="mb-2.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-400 dark:text-zinc-500">
                <Database size={12} />
                知识库
              </div>
              <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="flex items-center justify-between">
                  <span className="text-[13px] text-zinc-600 dark:text-zinc-300">
                    财报知识库
                  </span>
                  <Badge tone={kbInfo.ragChunks ? 'success' : 'neutral'}>
                    {kbInfo.ragChunks ? `${kbInfo.ragChunks} 块` : '未触发'}
                  </Badge>
                </div>
                <div className="mt-2 text-[11.5px] leading-relaxed text-zinc-400 dark:text-zinc-500">
                  {kbInfo.sources.length
                    ? `最近检索来源：${kbInfo.sources.join('、')}`
                    : '检索在服务端按 RAG_CORPUS_DIR 摄取，文档数与向量库状态暂未提供端点。'}
                </div>
                <button
                  type="button"
                  disabled
                  className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-zinc-300 py-2 text-[12.5px] text-zinc-400 dark:border-zinc-700 dark:text-zinc-500"
                  title="服务端摄取，待接入"
                >
                  <CloudArrowUp size={15} />
                  上传文档
                  <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
                    待接入
                  </span>
                </button>
              </div>
            </section>
          </div>
        </aside>
      )}
    </>
  )
}
