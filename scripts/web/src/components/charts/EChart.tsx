import { memo, useEffect, useRef } from 'react'
import { cn } from '@/lib/cn'
import { echarts, type ChartOption, type EChartsType } from './echarts-setup'

/**
 * ECharts 的 React 外壳（全站唯一处碰命令式 API）。
 *
 * 三条不变量：
 * ① 容器必须有确定高度——ECharts 读 clientHeight，0 高度会静默画空图（不报错），
 *    所以调用方必须在 className 里给出实际高度（如 h-[200px]），不能只给 h-full 又指望父级撑开；
 * ② StrictMode 双挂载：cleanup 必须真 dispose，否则第二次 init 打到同一 DOM 会
 *    console.error「There is a chart instance already initialized on the dom」并泄漏实例；
 * ③ 主题色全部写在 option 里（见 useChartTokens），所以浅/深色切换只需 setOption，
 *    不需要 dispose + 重新 init。
 */
type Props = {
  option: ChartOption
  /** 容器高度类（必填心智：h-[200px] / h-full 之类，不给高度就没有图） */
  className?: string
  /** canvas 对读屏不可见，必须给文字替代 */
  ariaLabel: string
  /** 默认 notMerge：换数据/换主题时旧 series 不残留（合并语义在多序列折线上极易出错） */
  notMerge?: boolean
}

function EChart({ option, className, ariaLabel, notMerge = true }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<EChartsType | null>(null)

  useEffect(() => {
    const el = hostRef.current
    if (!el) return

    // 防御：HMR 或 StrictMode 残留实例（正常路径下 cleanup 已 dispose，这里是兜底）
    echarts.getInstanceByDom(el)?.dispose()
    const chart = echarts.init(el, undefined, { renderer: 'canvas' })
    chartRef.current = chart

    // 宽度变化来源：侧栏折叠 / ConfigPanel 开合 / 窗口缩放 / md 断点切换。
    // rAF 合并两个原因：避免 "ResizeObserver loop completed with undelivered notifications"，
    // 且 resize() 本身会改布局 → 可能再次触发 RO。
    let raf = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const c = chartRef.current
        if (!c || c.isDisposed()) return
        if (el.clientWidth > 0 && el.clientHeight > 0) c.resize()
      })
    })
    ro.observe(el)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const c = chartRef.current
    if (!c || c.isDisposed()) return
    // **刻意不加 lazyUpdate**：它把这次更新推迟到 requestAnimationFrame，而 canvas 元素本身
    // 是在首次 setOption 时才创建的（init 只建外层 div）——于是在 rAF 不跳动的场合
    // （标签页隐藏、窗口最小化、被节流的内嵌预览）图表会永远停在空白 div，且不报任何错。
    // 这里每次数据/主题变化只调一次 setOption，不是热循环，lazyUpdate 换不来什么，风险却是静默空图。
    c.setOption(option, { notMerge })
  }, [option, notMerge])

  return <div ref={hostRef} role="img" aria-label={ariaLabel} className={cn('w-full', className)} />
}

export default memo(EChart)
