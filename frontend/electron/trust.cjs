/**
 * T-019 Electron 下载地址可信 origin 白名单（纯函数，不依赖 electron，可被 vitest 直接测试）。
 *
 * 主进程只接受精确 origin 匹配的 http(s) 下载地址，防止渲染进程被注入后把桌面端
 * 当作任意地址请求/下载的跳板（SSRF、自动打开不可信文件）。
 */

'use strict'

// 后端默认监听 127.0.0.1:8001（src/config.py），本地联调两个回环主机名都放行。
const DEFAULT_TRUSTED_ORIGINS = [
  'http://localhost:8001',
  'http://127.0.0.1:8001',
]

/**
 * 把单个候选值标准化为 http(s) origin；非法或协议不符返回 null。
 * URL.origin 会归一化主机名大小写与默认端口，避免字符串匹配类绕过。
 */
function toOrigin(value) {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (!trimmed) return null
  let url
  try {
    url = new URL(trimmed)
  } catch {
    return null
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
  return url.origin
}

/**
 * 解析逗号分隔的环境变量（ELECTRON_TRUSTED_ORIGINS）：
 * 逐项 trim、标准化、跳过非法项、按出现顺序去重。
 */
function parseTrustedOrigins(raw) {
  if (typeof raw !== 'string' || !raw.trim()) return []
  const origins = []
  for (const item of raw.split(',')) {
    const origin = toOrigin(item)
    if (origin && !origins.includes(origin)) origins.push(origin)
  }
  return origins
}

/** 默认回环白名单与环境变量白名单合并去重。 */
function resolveTrustedOrigins(envValue) {
  const merged = [...DEFAULT_TRUSTED_ORIGINS]
  for (const origin of parseTrustedOrigins(envValue)) {
    if (!merged.includes(origin)) merged.push(origin)
  }
  return merged
}

/**
 * 下载地址是否命中可信 origin：必须是绝对 http(s) URL 且 origin 精确相等。
 * 任何异常、非字符串、协议不符或未命中都返回 false。
 */
function isTrustedUrl(target, allowedOrigins) {
  if (typeof target !== 'string' || !target.trim()) return false
  let url
  try {
    url = new URL(target)
  } catch {
    return false
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false
  return Array.isArray(allowedOrigins) && allowedOrigins.includes(url.origin)
}

module.exports = {
  DEFAULT_TRUSTED_ORIGINS,
  parseTrustedOrigins,
  resolveTrustedOrigins,
  isTrustedUrl,
}
