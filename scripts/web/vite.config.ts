import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/chat': { target: 'http://localhost:8010', changeOrigin: true },
      '/api': { target: 'http://localhost:8010', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'remark-gfm'],
          icons: ['@phosphor-icons/react'],
          // ECharts 只在快报页用到 → 单独成块 + 由 components/charts/EChart 走 React.lazy 动态导入，
          // 只在打开快报页时才拉（已验证 index.html 不 modulepreload 它、入口块也不静态 import 它）。
          // 四个子路径都列上，别只写 'echarts/core'——那样 charts/components/renderers 会漏进入口块。
          // 实测 538KB raw / 180KB gzip：这就是 core + Line/Bar + Grid/Tooltip/Legend/DataZoom/MarkLine
          // + CanvasRenderer + zrender 的真实体积（tree-shaking 是生效的，换函数式归块产物逐字节相同）。
          echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
          radix: [
            '@radix-ui/react-dialog',
            '@radix-ui/react-dropdown-menu',
            '@radix-ui/react-switch',
            '@radix-ui/react-tooltip',
            '@radix-ui/react-slot',
          ],
        },
      },
    },
  },
})
