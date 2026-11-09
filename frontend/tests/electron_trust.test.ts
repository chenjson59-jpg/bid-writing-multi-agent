// @vitest-environment node
/**
 * T-019 Electron HTTP 下载可信 origin 白名单测试。
 * 纯 node 环境：只测 electron/trust.cjs 的纯函数与 main.cjs 的源码级安全守卫，
 * 不启动 Electron、不依赖渲染进程。
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import trust from '../electron/trust.cjs'

const {
  DEFAULT_TRUSTED_ORIGINS,
  parseTrustedOrigins,
  resolveTrustedOrigins,
  isTrustedUrl,
} = trust as {
  DEFAULT_TRUSTED_ORIGINS: string[]
  parseTrustedOrigins: (raw: unknown) => string[]
  resolveTrustedOrigins: (raw: unknown) => string[]
  isTrustedUrl: (target: unknown, allowedOrigins: string[]) => boolean
}

describe('parseTrustedOrigins 环境变量解析', () => {
  it('逗号分隔、trim、去重并标准化为 origin', () => {
    const origins = parseTrustedOrigins(
      ' http://example.com:8001 ,http://example.com:8001, https://secure.example.cn/',
    )
    expect(origins).toEqual([
      'http://example.com:8001',
      'https://secure.example.cn',
    ])
  })

  it('默认端口被 URL 标准化', () => {
    expect(parseTrustedOrigins('http://host:80,https://host:443/path')).toEqual([
      'http://host',
      'https://host',
    ])
  })

  it('跳过非法与非 http(s) 条目', () => {
    expect(
      parseTrustedOrigins('not-a-url,file:///etc/passwd,http://ok.local:8001'),
    ).toEqual(['http://ok.local:8001'])
  })

  it('非字符串、空字符串与空白输入返回空数组', () => {
    expect(parseTrustedOrigins(undefined)).toEqual([])
    expect(parseTrustedOrigins(null)).toEqual([])
    expect(parseTrustedOrigins(123)).toEqual([])
    expect(parseTrustedOrigins('   ')).toEqual([])
  })
})

describe('resolveTrustedOrigins 默认白名单合并', () => {
  it('未配置时包含本机回环后端两个 origin', () => {
    const origins = resolveTrustedOrigins(undefined)
    expect(origins).toEqual(DEFAULT_TRUSTED_ORIGINS)
    expect(origins).toContain('http://localhost:8001')
    expect(origins).toContain('http://127.0.0.1:8001')
  })

  it('环境变量 origin 与默认值合并去重', () => {
    const origins = resolveTrustedOrigins(
      'http://localhost:8001,http://47.117.189.10:8001',
    )
    expect(origins).toHaveLength(3)
    expect(origins).toContain('http://47.117.189.10:8001')
  })
})

describe('isTrustedUrl 下载地址校验', () => {
  const allowed = resolveTrustedOrigins('http://47.117.189.10:8001')

  it('默认回环与配置的远程后端放行（路径/查询不影响判定）', () => {
    expect(
      isTrustedUrl('http://localhost:8001/api/bids/x/files/bid.docx?download=true', allowed),
    ).toBe(true)
    expect(
      isTrustedUrl('http://47.117.189.10:8001/api/bids/x/files/report.json', allowed),
    ).toBe(true)
  })

  it('任意外部 origin 与内网其他主机拒绝', () => {
    expect(isTrustedUrl('http://evil.com/x.docx', allowed)).toBe(false)
    expect(isTrustedUrl('http://192.168.1.1:8001/x', allowed)).toBe(false)
    expect(isTrustedUrl('https://localhost:8001/x', allowed)).toBe(false)
    expect(isTrustedUrl('http://localhost:9001/x', allowed)).toBe(false)
  })

  it('字符串后缀伪装不允许绕过', () => {
    expect(isTrustedUrl('http://localhost:8001.evil.com/x', allowed)).toBe(false)
    expect(isTrustedUrl('http://evil.com/?localhost:8001', allowed)).toBe(false)
  })

  it('userinfo 伪装按真实 origin 判定并拒绝', () => {
    expect(
      isTrustedUrl('http://localhost:8001@evil.com/x.docx', allowed),
    ).toBe(false)
  })

  it('非 http(s) 协议一律拒绝', () => {
    expect(isTrustedUrl('file:///c:/windows/system32/cmd.exe', allowed)).toBe(false)
    expect(isTrustedUrl('javascript:alert(1)', allowed)).toBe(false)
    expect(isTrustedUrl('data:text/html,xxx', allowed)).toBe(false)
  })

  it('非字符串、空串、相对路径与畸形 URL 拒绝', () => {
    expect(isTrustedUrl(undefined, allowed)).toBe(false)
    expect(isTrustedUrl(null, allowed)).toBe(false)
    expect(isTrustedUrl('', allowed)).toBe(false)
    expect(isTrustedUrl('/api/bids/x/files/bid.md', allowed)).toBe(false)
    expect(isTrustedUrl('ht!tp://broken', allowed)).toBe(false)
  })
})

describe('main.cjs 安全配置守卫', () => {
  const mainSource = readFileSync(
    fileURLToPath(new URL('../electron/main.cjs', import.meta.url)),
    'utf-8',
  )

  it('保持 contextIsolation=true 且 nodeIntegration=false', () => {
    expect(mainSource).toMatch(/contextIsolation:\s*true/)
    expect(mainSource).toMatch(/nodeIntegration:\s*false/)
  })

  it('HTTP 下载前必须经过可信 origin 校验', () => {
    expect(mainSource).toMatch(/require\(['"]\.\/trust\.cjs['"]\)/)
    // 只考察 open-path 处理器：下载函数定义本身在处理器之前，不能用全文位置比较
    const handlerStart = mainSource.indexOf("ipcMain.handle('desktop:open-path'")
    expect(handlerStart).toBeGreaterThan(-1)
    const handler = mainSource.slice(handlerStart)
    const guardIndex = handler.indexOf('isTrustedUrl(')
    const downloadIndex = handler.indexOf('downloadToTemp(')
    expect(guardIndex).toBeGreaterThan(-1)
    expect(downloadIndex).toBeGreaterThan(-1)
    expect(guardIndex).toBeLessThan(downloadIndex)
    // 校验失败必须短路返回，不进入下载
    expect(handler).toContain('下载地址不在可信后端范围内')
  })
})
