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

// ---------- 页面任务视图的受控派生（T-031：选择态上移 App 后，页面只做派生） ----------

/** 页面任务视图三态：表单 / 已提交待首帧 / 展示 job。 */
export type PageJobState =
  | { mode: 'form' }
  | { mode: 'submitting'; jobId: string }
  | { mode: 'job'; job: JobView }

/**
 * 由 App 受控选择态与 poll 回来的 activeJob 派生页面任务视图。
 * - 未选择 → 表单；
 * - 已选择但 activeJob 缺失或 job_id 不匹配（首帧未返回 / 旧 job 残留）→ submitting，
 *   绝不渲染 id 不匹配的旧 job，防止任务串台；
 * - id 匹配 → 展示该 job。
 */
export function derivePageJobState(
  selectedJobId: string | null,
  activeJob: JobView | null,
): PageJobState {
  if (!selectedJobId) return { mode: 'form' }
  if (activeJob && activeJob.job_id === selectedJobId) return { mode: 'job', job: activeJob }
  return { mode: 'submitting', jobId: selectedJobId }
}

/** 「查看」按钮跳转决策：切 tab 还是原地聚焦。 */
export interface JobJumpDecision {
  tab: Tab
  changedTab: boolean
}

export function planJobJump(kind: string, currentTab: Tab): JobJumpDecision {
  const tab = kindTab(kind)
  return { tab, changedTab: tab !== currentTab }
}

// ---------- 终态生命周期（T-030：终态留存、即停轮询、显式复位、历史页刷新信号） ----------

/** 一次 poll 观测后的槽位决策。 */
export interface PollOutcome {
  /** 本次响应已终态：该槽位立即停止轮询，但终态快照保留在 activeJobs 中等用户显式复位。 */
  stopPolling: boolean
  /** 是否发生「非终态 → 终态」迁移（prev 为 null 的首帧即终态也算），用于驱动历史页刷新。 */
  terminalTransition: boolean
}

/**
 * 由上一次观测值与本次 getJob 响应派生轮询决策：
 * - 终态即停轮询，不再靠 grace 定时器清空状态来间接停止；
 * - 仅在非终态 → 终态的迁移点报告 terminalTransition，
 *   同一终态被再次观测不会重复产生事件（防重复刷新）。
 */
export function decidePollOutcome(
  prev: JobView | null | undefined,
  next: JobView,
): PollOutcome {
  const terminal = isTerminal(next)
  return {
    stopPolling: terminal,
    terminalTransition: terminal && !isTerminal(prev),
  }
}

/** 历史页刷新信号：只有终态迁移确实发生时才自增，无迁移保持不变（无空转）。 */
export function nextHistoryRefreshNonce(current: number, terminalTransition: boolean): number {
  return terminalTransition ? current + 1 : current
}

/**
 * 页面成功回调（onUploaded/onWritten 侧栏计数刷新）是否应对当前选择态触发：
 * 选择态与 activeJob 的 job_id 匹配、状态为 succeeded，且本 jobId 尚未触发过。
 * 按 jobId 记账而非布尔闩锁：用户复位后提交的新任务成功时可以再次触发。
 */
export function shouldEmitJobSuccess(
  activeJob: JobView | null | undefined,
  selectedJobId: string | null,
  firedJobId: string | null,
): boolean {
  if (!activeJob || !selectedJobId) return false
  if (activeJob.job_id !== selectedJobId) return false
  if (activeJob.status !== 'succeeded') return false
  return firedJobId !== activeJob.job_id
}

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
