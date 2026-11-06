/** 接口基址解析测试：后端地址只能来自构建期环境变量，代码中不得内置公网主机。 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import apiSource from '../src/api?raw'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

describe('apiUrl 接口基址解析', () => {
  it('未配置 VITE_API_BASE 时使用相对路径，走同源或 Vite 代理', async () => {
    vi.stubEnv('VITE_API_BASE', '')
    const { apiUrl } = await import('../src/api')
    expect(apiUrl('/api/health')).toBe('/api/health')
  })

  it('配置 VITE_API_BASE 后拼接为绝对地址', async () => {
    vi.stubEnv('VITE_API_BASE', 'https://api.example.com')
    const { apiUrl } = await import('../src/api')
    expect(apiUrl('/api/health')).toBe('https://api.example.com/api/health')
  })

  it('源码中不得硬编码 IP 形式的主机地址', () => {
    expect(apiSource).not.toContain('47.117.189.10')
    expect(apiSource).not.toMatch(/https?:\/\/\d{1,3}(?:\.\d{1,3}){3}/)
  })
})
