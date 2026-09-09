/**
 * ECharts 唯一注册点（tree-shaken）：只 use() 快报实际用到的 chart/component，
 * 其余（地图/雷达/toolbox/3D…）不进 bundle。
 *
 * 新增图表类型必须先在这里登记：ECharts 对未注册的 series.type 只 console.error，
 * 页面静默画空图，tsc 抓不到（ComposeOption 只约束 option 形状，不约束注册）。
 *
 * 不从 'echarts' 根导入任何值——那会把全量 bundle 拉进来（tree-shaking 失效）。
 * 子路径的类型声明（core.d.ts / charts.d.ts / components.d.ts / renderers.d.ts）
 * 与包一起发布，moduleResolution:"bundler" 下能正常解析。
 */
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import {
  DataZoomInsideComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { ComposeOption, EChartsType } from 'echarts/core'
import type { BarSeriesOption, LineSeriesOption } from 'echarts/charts'
import type {
  DataZoomComponentOption,
  GridComponentOption,
  LegendComponentOption,
  MarkLineComponentOption,
  TooltipComponentOption,
} from 'echarts/components'

echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomInsideComponent, // 只要 inside（拖拽/滚轮缩放），不要 slider（省体积且不占垂直空间）
  MarkLineComponent, // 指数走势的「与首日持平」基准线
  CanvasRenderer, // 不用 SVGRenderer：多序列 + 上百点时 canvas 明显更省
])

/** 快报图表的 option 联合类型：只包含已注册的模块，写错 series.type 直接编译失败。 */
export type ChartOption = ComposeOption<
  | LineSeriesOption
  | BarSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | LegendComponentOption
  | DataZoomComponentOption
  | MarkLineComponentOption
>

export { echarts }
export type { EChartsType }
