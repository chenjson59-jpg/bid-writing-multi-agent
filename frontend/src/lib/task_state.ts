/** 全局任务状态相关的纯函数（无 React / 无副作用）。
 *
 * App.tsx 的 GlobalTaskBar、runningParse/runningBid 派生状态、
 * UploadPage/WritePage 的 busy 判断，全部走这里。写测试只测这些函数就行。
 */
import type { JobStatus, JobView } from '../types'

// T-034：内部登记的四类任务；JobView.kind 仍是 string，条目 kind 只由四个登记入口字面量写入
export type JobKind = 'parse' | 'bid' | 'supplement' | 'rewrite'
export type Tab = 'upload' | 'library' | 'write' | 'history'
// 承载任务视图的页面通道：parse 归 upload，bid/supplement/rewrite 归 write
export type PageChannel = 'upload' | 'write'

/** 全局集合中的单个跟踪任务（数组元素，顺序即任务栏展示顺序，最新在前）。 */
export interface TrackedJob {
  jobId: string
  kind: JobKind
  /** 最近一次 getJob 快照；登记后首帧 poll 前为 null（对应 T-031 submitting 态）。 */
  job: JobView | null
  /** 终态已观测：停止轮询但快照留存，等待用户显式关闭。 */
  settled: boolean
}
export type JobCollection = TrackedJob[]

/** 终态迁移点触发的一次性回调（补写列表刷新 / 重写章节刷新等）。 */
export type SettledCallback = (job: JobView) => void

/** 两个页面通道的当前选中 jobId。 */
export type ChannelSelection = Record<PageChannel, string | null>

export interface RegisterJobInput {
  kind: JobKind
  jobId: string
  onSettled?: SettledCallback
  /**
   * 是否自动选中并置顶到所属页面通道。顶层提交（parse/bid）默认 true；
   * ResultView 发起的 supplement/rewrite 是依附当前产物视图的后台子任务，
   * 传 false 避免抢占页面选择而卸载注册回调的组件。
   */
  autoSelect?: boolean
}

/** App 下发给 ResultView 的任务桥接（登记/撤销回调/查快照），避免层层透传多个 props。 */
export interface JobBridge {
  track: (input: RegisterJobInput) => void
  revoke: (jobId: string) => void
  find: (jobId: string) => JobView | null
  has: (jobId: string) => boolean
}

/** ResultView 本地跟踪的补写/重写子任务状态。 */
export type SubTaskState = 'idle' | 'running' | 'done' | 'failed' | 'closed'

/**
 * 由 ResultView 本地 jobId、全局集合是否仍保留条目与最新快照派生五态。
 * 显式关闭会移除全局条目但不清空本地 jobId，此场景单独归位为 closed，
 * 不能再按「有 id 但快照缺失」误判为 running。
 */
export function deriveSubTaskState(
  trackedId: string | null,
  exists: boolean,
  job: JobView | null | undefined,
): SubTaskState {
  if (!trackedId) return 'idle'
  if (!exists) return 'closed'
  if (!job || job.status === 'pending' || job.status === 'running') return 'running'
  return job.status === 'succeeded' ? 'done' : 'failed'
}

export const EMPTY_SELECTION: ChannelSelection = { upload: null, write: null }

// ---------- 状态判断 ----------

/** 任务是否处于"运行中"（pending/running 都算正在做，succeeded/failed 算终态）。 */
export function isRunning(job: JobView | null | undefined): boolean {
  if (!job) return false
  return job.status === 'pending' || job.status === 'running'
}

/** 任务是否已经到达终态（succeeded/failed）；终态即停轮询、快照留存到显式关闭，无 grace 定时清理。 */
export function isTerminal(job: JobView | null | undefined): boolean {
  if (!job) return false
  return job.status === 'succeeded' || job.status === 'failed'
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

// T-034 决策七：补写/重写在任务栏独立成标签；未知 kind 原样返回，不臆造
export function kindLabel(kind: string): string {
  if (kind === 'parse') return '解析'
  if (kind === 'bid') return '编写'
  if (kind === 'supplement') return '补写'
  if (kind === 'rewrite') return '重写'
  return kind || '任务'
}

/** kind → 页面通道：只有 parse 归 upload，其余一律 write。 */
export function jobChannel(kind: string): PageChannel {
  return kind === 'parse' ? 'upload' : 'write'
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

// ---------- 安全更新：相同状态不触发重渲染 ----------

/** 逐项比较进度窗口；null/undefined 按既有长度兜底语义视为空数组。 */
function progressEquals(
  a: string[] | null | undefined,
  b: string[] | null | undefined,
): boolean {
  const left = a ?? []
  const right = b ?? []
  return left.length === right.length && left.every((message, index) => message === right[index])
}

/**
 * 新旧 JobView 比较器：job_id、status 或 progress 内容变化才算"真的变了"。
 *
 * 方案取舍：只比较末条依赖 progress 严格 append-only 的隐含约束；后端快照固定保留
 * 最近 50 条，窗口滚动后仍可能出现中间内容变化。50 条短字符串在 1.5s 轮询频率下
 * 逐项比较成本可忽略，且边角更少，因此选择整体逐项比较。
 */
export function jobEquals(a: JobView | null, b: JobView | null): boolean {
  if (a === b) return true
  if (!a || !b) return false
  if (a.job_id !== b.job_id) return false
  if (a.status !== b.status) return false
  return progressEquals(a.progress, b.progress)
}

// ============================================================
// T-034 多任务集合：以 job_id 为键的有序数组（最新在前），纯函数无 React 依赖
// ============================================================

/**
 * 登记新任务：前插置顶；同 jobId 已存在时防御性替换（正常流程不会重复登记）。
 * 旧单槽 Record 的同类覆盖根因即由本函数的「追加而非替换」语义修复。
 */
export function registerTrackedJob(jobs: JobCollection, entry: TrackedJob): JobCollection {
  return [entry, ...jobs.filter((item) => item.jobId !== entry.jobId)]
}

/** 按 jobId 写入最新快照；jobEquals 相等或 id 不存在时返回原数组引用（渲染 bail-out）。 */
export function upsertJobSnapshot(
  jobs: JobCollection,
  jobId: string,
  job: JobView,
): JobCollection {
  const index = jobs.findIndex((item) => item.jobId === jobId)
  if (index < 0) return jobs
  if (jobEquals(jobs[index].job, job)) return jobs
  const next = jobs.slice()
  next[index] = { ...jobs[index], job }
  return next
}

/** 终态观测：置 settled=true 停止轮询，快照保留；重复标记返回原引用。 */
export function markJobSettled(jobs: JobCollection, jobId: string): JobCollection {
  const index = jobs.findIndex((item) => item.jobId === jobId)
  if (index < 0) return jobs
  if (jobs[index].settled) return jobs
  const next = jobs.slice()
  next[index] = { ...jobs[index], settled: true }
  return next
}

/** 显式关闭：从集合移除条目；id 不存在时返回原引用。 */
export function closeJobById(jobs: JobCollection, jobId: string): JobCollection {
  if (!jobs.some((item) => item.jobId === jobId)) return jobs
  return jobs.filter((item) => item.jobId !== jobId)
}

/** 按 jobId 查找条目；null/未知 id 返回 null。 */
export function findTrackedJob(
  jobs: JobCollection,
  jobId: string | null,
): TrackedJob | null {
  if (!jobId) return null
  return jobs.find((item) => item.jobId === jobId) ?? null
}

/** 仍需轮询的条目（settled=false），保持登记顺序。 */
export function pollableJobs(jobs: JobCollection): TrackedJob[] {
  return jobs.filter((item) => !item.settled)
}

/** 页面受控 activeJob：选中条目的快照；首帧为 null 或选中项不存在时返回 null。 */
export function selectedJobSnapshot(
  jobs: JobCollection,
  selectedId: string | null,
): JobView | null {
  return findTrackedJob(jobs, selectedId)?.job ?? null
}

/** 登记后把对应通道选择态指向新 jobId（自动选中）；同值返回原引用。 */
export function selectionAfterRegister(
  selection: ChannelSelection,
  channel: PageChannel,
  jobId: string,
): ChannelSelection {
  if (selection[channel] === jobId) return selection
  return { ...selection, [channel]: jobId }
}

/** 关闭任务后修正选择态：被关的正是选中项才回 null（页面回表单），否则原样返回。 */
export function selectionAfterClose(
  selection: ChannelSelection,
  channel: PageChannel,
  closedId: string,
): ChannelSelection {
  if (selection[channel] !== closedId) return selection
  return { ...selection, [channel]: null }
}

// ---------- 终态回调注册表（App 用 ref 持有 Map；操作对入参纯净，便于离线测试） ----------

/** 登记终态回调；同 jobId 覆盖以最新为准。 */
export function putSettledCallback(
  registry: Map<string, SettledCallback>,
  jobId: string,
  callback: SettledCallback,
): void {
  registry.set(jobId, callback)
}

/** 取出即删：终态迁移点只调用一次，重放终态拿到 undefined。 */
export function takeSettledCallback(
  registry: Map<string, SettledCallback>,
  jobId: string,
): SettledCallback | undefined {
  const callback = registry.get(jobId)
  if (callback !== undefined) registry.delete(jobId)
  return callback
}

/** 组件卸载撤销：任务继续全局轮询，但回调不再触达已卸载实例。 */
export function revokeSettledCallback(
  registry: Map<string, SettledCallback>,
  jobId: string,
): void {
  registry.delete(jobId)
}
