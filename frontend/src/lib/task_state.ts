/** 全局任务状态相关的纯函数（无 React / 无副作用）。
 *
 * App.tsx 的 GlobalTaskBar、runningParse/runningBid 派生状态、
 * UploadPage/WritePage 的 busy 判断，全部走这里。写测试只测这些函数就行。
 */
import type { JobStatus, JobView } from '../types'

export type JobKind = 'parse' | 'bid'
export type Tab = 'upload' | 'library' | 'write' | 'history'

// ---------- 状态判断 ----------

/** 任务是否处于"运行中"（pending/running 都算正在做，succeeded/failed 算终态）。 */
export function isRunning(job: JobView | null | undefined): boolean {
  if (!job) return false
  return job.status === 'pending' || job.status === 'running'
}

/** 任务是否已经到达终态（App poller 会在终态后保留 8s 再清理）。 */
export function isTerminal(job: JobView | null | undefined): boolean {
  if (!job) return false
  return job.status === 'succeeded' || job.status === 'failed'
}

/** 终态类型：成功 vs 失败（用于 UI 颜色）。非终态返回 null。 */
export function terminalOutcome(job: JobView | null | undefined): 'succeeded' | 'failed' | null {
  if (!job) return null
  if (job.status === 'succeeded') return 'succeeded'
  if (job.status === 'failed') return 'failed'
  return null
}

// ---------- 展示标签 ----------

export const STATUS_LABEL: Record<JobStatus, string> = {
  pending: '排队中',
  running: '运行中',
  succeeded: '已完成',
  failed: '失败',
}

/** status 英文 → 中文标签。未知值也返回其本身（兜底）。 */
export function statusLabel(status: string): string {
  return STATUS_LABEL[status as JobStatus] ?? status
}

export function kindLabel(kind: string): string {
  if (kind === 'parse') return '解析'
  if (kind === 'bid' || kind === 'supplement' || kind === 'rewrite') return '编写'
  return kind || '任务'
}

/** kind → 对应哪个 Tab（用于"查看"按钮跳转）。 */
export function kindTab(kind: string): Tab {
  if (kind === 'parse') return 'upload'
  return 'write'
}

/** 终态成功后，App poller 还会保留这么多毫秒让用户看到完成提示再清理。 */
export const TERMINAL_GRACE_MS = 8_000

// ---------- 过滤活跃任务 ----------

/** 从一组 JobView 候选里挑出"正在运行"的项。
 *  App 底部 GlobalTaskBar 用它决定显示几条。
 */
export function filterRunning(jobs: Array<JobView | null | undefined>): JobView[] {
  return jobs.filter((j): j is JobView => isRunning(j))
}

/**
 * 从全部候选里算出全局"是否有任何活跃任务"（App.tsx 决定底部 padding、蓝点闪烁）。
 *  等价于 jobs.some(isRunning)。
 */
export function hasRunning(jobs: Array<JobView | null | undefined>): boolean {
  return jobs.some(isRunning)
}

// ---------- 安全更新：相同状态不触发重渲染 ----------

/**
 * 新旧 JobView 比较器：只有 status 变了或 progress 长度变了才算"真的变了"。
 *  App.tsx 的 poller 每 1.5s 调一次，每次都 setState 会触发无意义重渲染。
 */
export function jobEquals(a: JobView | null, b: JobView | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  if (a.job_id !== b.job_id) return false
  if (a.status !== b.status) return false
  if ((a.progress?.length ?? 0) !== (b.progress?.length ?? 0)) return false
  return true
}

/** 根据 kind 挑选 activeJobs 里对应通道的任务。类型安全（返回 JobView | null）。 */
export function pickJobForKind(
  activeJobs: Partial<Record<JobKind, JobView | null>>,
  kind: JobKind,
): JobView | null {
  return activeJobs[kind] ?? null
}
