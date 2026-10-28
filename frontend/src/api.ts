// 接口访问封装：统一错误处理与 JSON 解析，供各页面复用。
import type {
  BidCreateBody,
  BidHistoryItem,
  BidderProfileItem,
  BidderProfileUpsert,
  DocumentSummary,
  HealthInfo,
  JobView,
  ParseAccepted,
} from './types'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// 状态码 → 可操作的提示：把「服务器拒绝」翻译成「哪一步没起来」
const STATUS_HINTS: Record<number, string> = {
  0: '无法连接后端：请确认 FastAPI 已启动（/api/health 可访问）',
  401: '访问令牌无效：请核对部署时的 API_TOKEN 与构建时的 VITE_API_TOKEN',
  404: '资源不存在：文档可能未完成抽取，或产物已被清理',
  413: '文件超过大小上限，请压缩或拆分后重试',
  422: '参数校验失败，请检查填写内容',
  429: '排队任务已达上限，请等待当前任务结束后重试',
  503: '任务队列不可用：请确认 Redis 在运行且 Celery worker 已启动',
}

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    // 404/422 的 detail 已经是业务侧具体原因（如「资料不存在」），直接用服务端文案
    if (e.status === 404 || e.status === 422) return e.message
    const hint = STATUS_HINTS[e.status]
    return hint ? `${hint}（${e.status}）` : e.message
  }
  if (e instanceof TypeError) {
    return STATUS_HINTS[0]
  }
  return e instanceof Error ? e.message : '未知错误'
}

// 打包后被 Electron 以 file:// 加载时，相对路径 /api 无法命中后端，
// 此时回退到本地 FastAPI 绝对地址；dev server 下仍走 vite proxy。
const API_BASE =
  typeof window !== 'undefined' && window.location.protocol === 'file:'
    ? 'http://localhost:8001'
    : ''

function apiUrl(path: string): string {
  return API_BASE + path
}

// 公网部署的访问令牌，构建时通过 VITE_API_TOKEN 注入；本地开发为空则不携带。
const API_TOKEN = (import.meta.env.VITE_API_TOKEN as string | undefined) || ''

function authHeaders(): Record<string, string> {
  return API_TOKEN ? { 'X-API-Token': API_TOKEN } : {}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(apiUrl(path), {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers ?? {}) },
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, await readError(resp))
  }
  return resp.json() as Promise<T>
}

async function readError(resp: Response): Promise<string> {
  try {
    const data = (await resp.json()) as { detail?: unknown }
    if (typeof data.detail === 'string') return data.detail
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => String((d as { msg?: string }).msg ?? d)).join('；')
    }
  } catch {
    // 响应体不是 JSON，退回到状态描述
  }
  return resp.statusText || `请求失败（${resp.status}）`
}

export function getHealth(): Promise<HealthInfo> {
  return request('/api/health')
}

export function listDocuments(): Promise<DocumentSummary[]> {
  return request('/api/documents')
}

export async function getMarkdown(docId: string): Promise<string> {
  const resp = await fetch(apiUrl(`/api/documents/${docId}/markdown`), {
    headers: authHeaders(),
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, await readError(resp))
  }
  return resp.text()
}

export function uploadParse(
  file: File,
  options: { parser: string; force: boolean; extract: boolean; ingest: boolean },
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<ParseAccepted> {
  const form = new FormData()
  form.append('file', file)
  const params = new URLSearchParams({
    parser: options.parser,
    force: String(options.force),
    extract: String(options.extract),
    ingest: String(options.ingest),
  })

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl(`/api/parse?${params.toString()}`))
    if (API_TOKEN) {
      xhr.setRequestHeader('X-API-Token', API_TOKEN)
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as ParseAccepted)
      } else {
        reject(new ApiError(xhr.status, readStatusError(xhr)))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, '网络错误'))
    xhr.onabort = () => reject(new DOMException('已取消', 'AbortError'))
    if (signal) {
      signal.addEventListener('abort', () => xhr.abort())
    }
    xhr.send(form)
  })
}

function readStatusError(xhr: XMLHttpRequest): string {
  try {
    const data = JSON.parse(xhr.responseText) as { detail?: unknown }
    if (typeof data.detail === 'string') return data.detail
  } catch {
    // 响应体不是 JSON
  }
  return xhr.statusText || `请求失败（${xhr.status}）`
}

export function getJob(jobId: string): Promise<JobView> {
  return request(`/api/jobs/${jobId}`)
}

export function createBid(body: BidCreateBody): Promise<ParseAccepted> {
  return request('/api/bids', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function listBids(): Promise<BidHistoryItem[]> {
  return request('/api/bids')
}

// 补写未覆盖评分因素：异步任务，完成后生成新产物（原产物保留）
export function createSupplement(bidId: string): Promise<ParseAccepted> {
  return request(`/api/bids/${bidId}/supplement`, { method: 'POST' })
}

// ---- 投标人资料库：一次录入、多次复用 ----

export function listProfiles(): Promise<BidderProfileItem[]> {
  return request('/api/profiles')
}

export function createProfile(body: BidderProfileUpsert): Promise<BidderProfileItem> {
  return request('/api/profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function updateProfile(
  id: number,
  body: BidderProfileUpsert,
): Promise<BidderProfileItem> {
  return request(`/api/profiles/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function deleteProfile(id: number): Promise<void> {
  await request(`/api/profiles/${id}`, { method: 'DELETE' })
}

// 产物下载走浏览器导航，由响应头决定附件或内联；预览场景直接 fetch 文本。
// 导航类请求无法携带自定义头，令牌经 query 传递（服务端中间件同时接受两种方式）。
export function bidFileUrl(bidId: string, name: string, download: boolean): string {
  const url = apiUrl(`/api/bids/${bidId}/files/${name}?download=${download}`)
  return API_TOKEN ? `${url}&token=${encodeURIComponent(API_TOKEN)}` : url
}
