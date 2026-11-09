import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
  test: {
    // 组件测试依赖 DOM；纯函数测试在 jsdom 下同样可运行，无需拆分环境
    environment: 'jsdom',
    // 沿用既有测试的显式 import 风格（import { describe, it, expect } from 'vitest'），不开启全局 API
    globals: false,
    // 注册 jest-dom matcher 与 jsdom 缺失 API 的 mock，见 tests/setup.ts
    setupFiles: ['./tests/setup.ts'],
  },
})
