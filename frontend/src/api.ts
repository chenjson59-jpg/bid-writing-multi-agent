// 接口访问封装：统一错误处理与 JSON 解析，供各页面复用。
import type {
  BidCreateBody,
  BidHistoryItem,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, init)
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
  const resp = await fetch(`/api/documents/${docId}/markdown`)
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
    xhr.open('POST', `/api/parse?${params.toString()}`)
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

// 产物下载走浏览器导航，由响应头决定附件或内联；预览场景直接 fetch 文本。
export function bidFileUrl(bidId: string, name: string, download: boolean): string {
  return `/api/bids/${bidId}/files/${name}?download=${download}`
}
