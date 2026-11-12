import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getHealth, getJob, listBids, listDocuments } from './api'
import { HistoryPage } from './pages/HistoryPage'
import { LibraryPage } from './pages/LibraryPage'
import { UploadPage } from './pages/UploadPage'
import { WritePage } from './pages/WritePage'
import {
  closeJobById,
  decidePollOutcome,
  EMPTY_SELECTION,
  findTrackedJob,
  isRunning,
  jobChannel,
  kindLabel,
  markJobSettled,
  nextHistoryRefreshNonce,
  pollableJobs,
  putSettledCallback,
  registerTrackedJob,
  revokeSettledCallback,
  selectedJobSnapshot,
  selectionAfterClose,
  selectionAfterRegister,
  statusLabel,
  takeSettledCallback,
  upsertJobSnapshot,
  type ChannelSelection,
  type JobBridge,
  type JobCollection,
  type PageChannel,
  type RegisterJobInput,
  type SettledCallback,
  type Tab,
  type TrackedJob,
} from './lib/task_state'
import type { HealthInfo, JobView } from './types'

type Theme = 'dark' | 'light'
type HealthState = 'ok' | 'down' | 'idle'

function loadTheme(): Theme {
  try {
    return localStorage.getItem('theme') === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

// 状态点：绿=正常、红=异常、灰=不适用/未知
function StatusRow({ label, state, detail }: { label: string; state: HealthState; detail?: string }) {
  const tone: Record<HealthState, string> = {
    ok: 'bg-emerald-400',
    down: 'bg-rose-500',
    idle: 'bg-slate-500',
  }
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex items-center gap-2 text-slate-400">
        <span className={`h-2 w-2 rounded-full ${tone[state]}`} aria-hidden="true" />
        {label}
      </span>
      <span className="truncate text-slate-500">{detail ?? (state === 'ok' ? '正常' : '异常')}</span>
    </div>
  )
}

// 全局任务进度条：fixed 贴底，任何页面都能看到，切 tab 不会丢。
// T-034：渲染多任务集合全部条目（含终态留存行），终态行提供显式「关闭」。
function GlobalTaskBar({
  jobs,
  onJumpTo,
  onClose,
}: {
  jobs: TrackedJob[]
  onJumpTo: (jobId: string) => void
  onClose: (jobId: string) => void
}) {
  if (jobs.length === 0) return null
  const runningCount = jobs.filter((entry) => isRunning(entry.job)).length
  const pendingCount = jobs.filter((entry) => entry.job?.status === 'pending').length

  return (
    <div className="pointer-events-none fixed bottom-0 left-0 right-0 z-50 flex justify-center p-3">
      <div className="pointer-events-auto w-[min(720px,92vw)] rounded-lg border border-sky-600/40 bg-slate-900/95 shadow-xl shadow-sky-900/30 backdrop-blur">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2 text-xs">
          <span className="flex items-center gap-1 text-sky-400">
            <span className="h-2 w-2 animate-pulse rounded-full bg-sky-400" />
            任务 {jobs.length} 个
            {runningCount > 0 && <span className="text-slate-400">· 运行中 {runningCount}</span>}
            {pendingCount > 0 && <span className="text-amber-400">· 排队 {pendingCount}</span>}
          </span>
        </div>
        <ul>
          {jobs.map((entry) => {
            const job = entry.job
            const status = job?.status ?? null
            return (
              <li
                key={entry.jobId}
                className="flex items-center gap-3 border-b border-slate-800/70 px-4 py-2 text-sm last:border-b-0"
              >
                <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                  {kindLabel(entry.kind)}
                </span>
                <span className="flex-1 truncate text-slate-300">
                  {job?.source_name || '任务 ' + entry.jobId.slice(0, 8)}
                </span>
                <span
                  className={
                    status === 'running'
                      ? 'text-sky-400'
                      : status === 'pending'
                        ? 'text-amber-400'
                        : status === 'succeeded'
                          ? 'text-emerald-400'
                          : status === 'failed'
                            ? 'text-rose-400'
                            : 'text-amber-400'
                  }
                >
                  {job ? statusLabel(job.status) : '提交中'}
                </span>
                {job?.status === 'running' && job.progress.length > 0 && (
                  <span className="max-w-[220px] truncate text-xs text-slate-500">
                    {job.progress[job.progress.length - 1]}
                  </span>
                )}
                <button
                  type="button"
                  className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                  onClick={() => onJumpTo(entry.jobId)}
                >
                  查看
                </button>
                {entry.settled && (
                  <button
                    type="button"
                    className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                    onClick={() => onClose(entry.jobId)}
                  >
                    关闭
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

export default function App() {
  const [tab, setTab] = useState<Tab>('upload')
  const [theme, setTheme] = useState<Theme>(loadTheme)
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)
  const [docCount, setDocCount] = useState(0)
  const [bidCount, setBidCount] = useState(0)
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [healthDown, setHealthDown] = useState(false)

  // ---------- T-034 全局多任务集合：App 永远存活，切 tab 不卸载 ----------
  // 有序数组（最新登记在前），以 job_id 为身份；终态条目留存到显式关闭
  const [jobs, setJobs] = useState<JobCollection>([])
  // 两个页面通道各自的当前选中 jobId（parse→upload，bid/supplement/rewrite→write）
  const [selection, setSelection] = useState<ChannelSelection>(EMPTY_SELECTION)
  // 「查看」聚焦信号：按通道自增，页面 effect 据此滚动/高亮任务卡片
  const [focusNonces, setFocusNonces] = useState<Record<PageChannel, number>>({
    upload: 0,
    write: 0,
  })
  // T-030：终态事件驱动的历史页刷新信号，任意条目终态迁移时自增（无任务不空转）
  const [historyRefreshNonce, setHistoryRefreshNonce] = useState(0)

  // poller 与回调通过 ref 读最新集合，避免 interval 闭包陈旧
  const jobsRef = useRef<JobCollection>([])
  const selectionRef = useRef<ChannelSelection>(EMPTY_SELECTION)
  useEffect(() => {
    jobsRef.current = jobs
  }, [jobs])
  useEffect(() => {
    selectionRef.current = selection
  }, [selection])
  // 每个 jobId 的上一次观测值（终态迁移判定）；登记时置 null，关闭时删除
  const pollPrevRef = useRef<Map<string, JobView | null>>(new Map())
  // 终态一次性回调注册表（补写/重写的数据刷新），闭包不进 React state
  const settledCallbacksRef = useRef<Map<string, SettledCallback>>(new Map())

  // 登记新任务：追加置顶 + 通道自动选中；可携带终态一次性回调
  const registerJob = useCallback((input: RegisterJobInput) => {
    pollPrevRef.current.set(input.jobId, null)
    if (input.onSettled) {
      putSettledCallback(settledCallbacksRef.current, input.jobId, input.onSettled)
    }
    const entry: TrackedJob = { jobId: input.jobId, kind: input.kind, job: null, settled: false }
    // 同步刷新 ref：poller 不能依赖 effect 时序，否则注册后首个 tick 可能漏轮询
    const nextJobs = registerTrackedJob(jobsRef.current, entry)
    jobsRef.current = nextJobs
    setJobs(nextJobs)
    // 顶层提交自动选中；补写/重写等后台子任务不抢占当前页面选择（autoSelect=false）
    if (input.autoSelect !== false) {
      setSelection((prev) => selectionAfterRegister(prev, jobChannel(input.kind), input.jobId))
    }
  }, [])

  // 组件卸载时撤销终态回调（任务本身继续全局轮询），不操作已卸载组件
  const revokeSettled = useCallback((jobId: string) => {
    revokeSettledCallback(settledCallbacksRef.current, jobId)
  }, [])

  // 显式关闭指定任务：集合 / 观测值 / 回调 / 选中态级联清理
  const closeJob = useCallback((jobId: string) => {
    const tracked = findTrackedJob(jobsRef.current, jobId)
    pollPrevRef.current.delete(jobId)
    revokeSettledCallback(settledCallbacksRef.current, jobId)
    if (tracked) {
      setSelection((prev) => selectionAfterClose(prev, jobChannel(tracked.kind), jobId))
    }
    const nextJobs = closeJobById(jobsRef.current, jobId)
    jobsRef.current = nextJobs
    setJobs(nextJobs)
  }, [])

  // 页面复位按钮：关闭当前通道选中的任务并回表单，不自动选中集合内其他任务
  const closeSelectedOnChannel = useCallback(
    (channel: PageChannel) => {
      const jobId = selectionRef.current[channel]
      if (jobId) closeJob(jobId)
    },
    [closeJob],
  )

  // 任务进行中返回表单：只清选择态，任务继续后台轮询、任务栏可达，便于再次提交
  const deselectChannel = useCallback((channel: PageChannel) => {
    const next = { ...selectionRef.current, [channel]: null }
    selectionRef.current = next
    setSelection(next)
  }, [])

  // 统一 poller：1.5s 遍历全部未终态条目，逐个 getJob，终态即停且快照留存
  useEffect(() => {
    const tick = async () => {
      for (const tracked of pollableJobs(jobsRef.current)) {
        const { jobId } = tracked
        try {
          const job = await getJob(jobId)
          const prev = pollPrevRef.current.get(jobId) ?? null
          // T-030 纯函数：终态即停 + 仅在非终态→终态迁移点报告事件
          const outcome = decidePollOutcome(prev, job)
          pollPrevRef.current.set(jobId, job)
          setJobs((prev) => upsertJobSnapshot(prev, jobId, job))
          if (outcome.terminalTransition) {
            setHistoryRefreshNonce((n) => nextHistoryRefreshNonce(n, true))
            // 回调在 setState updater 之外调用，规避 StrictMode 双调用；take 即删保证一次
            const callback = takeSettledCallback(settledCallbacksRef.current, jobId)
            if (callback) {
              try {
                callback(job)
              } catch (error) {
                console.error('settled callback failed', error)
              }
            }
          }
          if (outcome.stopPolling) {
            setJobs((prev) => markJobSettled(prev, jobId))
          }
        } catch {
          // poll 失败（网络抖动）静默跳过，prev/settled 不变，下一轮继续
        }
      }
    }
    const timer = window.setInterval(tick, 1500)
    return () => window.clearInterval(timer)
  }, [])

  // 派生：运行中 → 左侧 nav 蓝点（按页面通道聚合）
  const runningParse = jobs.some(
    (entry) => jobChannel(entry.kind) === 'upload' && isRunning(entry.job),
  )
  const runningBid = jobs.some(
    (entry) => jobChannel(entry.kind) === 'write' && isRunning(entry.job),
  )

  // ResultView 经此桥接登记 supplement/rewrite 任务、撤销回调、查全局快照
  const jobBridge = useMemo<JobBridge>(
    () => ({
      track: (input) => registerJob(input),
      revoke: revokeSettled,
      find: (jobId) => selectedJobSnapshot(jobs, jobId),
      has: (jobId) => findTrackedJob(jobs, jobId) !== null,
    }),
    [jobs, registerJob, revokeSettled],
  )

  // ---------- 健康检查（每 5 秒） ----------
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const info = await getHealth()
        if (cancelled) return
        setHealth(info)
        setHealthDown(false)
      } catch {
        if (cancelled) return
        setHealth(null)
        setHealthDown(true)
      }
    }
    tick()
    const timer = window.setInterval(tick, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try {
      localStorage.setItem('theme', theme)
    } catch {
      // 存储不可用时忽略
    }
  }, [theme])

  const refreshCounts = useCallback(async () => {
    try {
      setDocCount((await listDocuments()).length)
      setBidCount((await listBids()).length)
    } catch {
      // 后端未连接时忽略，计数保持原值
    }
  }, [])

  useEffect(() => {
    refreshCounts()
  }, [refreshCounts])

  const goWrite = useCallback((docId: string) => {
    setSelectedDocId(docId)
    setTab('write')
  }, [])

  const navItems: { id: Tab; label: string; running: boolean; count: number | null }[] = [
    { id: 'upload', label: '上传解析', running: runningParse, count: null },
    { id: 'library', label: '文档库', running: false, count: docCount },
    { id: 'write', label: '编写标书', running: runningBid, count: null },
    { id: 'history', label: '任务与产物', running: false, count: bidCount },
  ]

  // local 后端没有 Redis，队列状态按「进程内」展示，不做红点告警
  const brokerState: HealthState =
    health && health.task_backend === 'local'
      ? 'idle'
      : health
        ? health.broker_ok
          ? 'ok'
          : 'down'
        : 'idle'

  // 底部任务栏「查看」：定位到具体 jobId——切通道 tab、选中该任务并触发聚焦高亮
  const jumpToJob = useCallback((jobId: string) => {
    const tracked = findTrackedJob(jobsRef.current, jobId)
    if (!tracked) return
    const channel = jobChannel(tracked.kind)
    const next = selectionAfterRegister(selectionRef.current, channel, jobId)
    selectionRef.current = next
    setSelection(next)
    setTab(channel)
    setFocusNonces((prev) => ({ ...prev, [channel]: prev[channel] + 1 }))
  }, [])

  // 页面底部 padding，防止被 GlobalTaskBar 遮住（终态留存行仍在任务栏）
  const bottomPad = jobs.length > 0 ? 'pb-24' : ''

  return (
    <div className={`flex min-h-screen bg-slate-950 text-slate-100 ${bottomPad}`}>
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-800 p-4">
        <h1 className="text-sm font-bold leading-5">招标文件多智能体系统</h1>
        <nav className="mt-6 flex-1 space-y-1">
          {navItems.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-sm ${
                tab === item.id ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:bg-slate-900'
              }`}
            >
              <span className="flex items-center gap-2">
                {item.label}
                {item.running && (
                  <span className="h-2 w-2 animate-pulse rounded-full bg-sky-400" aria-label="运行中" />
                )}
              </span>
              {item.count !== null && <span className="text-xs text-slate-500">{item.count}</span>}
            </button>
          ))}
        </nav>
        <div className="mt-4 space-y-1.5 rounded-md border border-slate-800 bg-slate-900/60 p-3 text-xs">
          <StatusRow
            label="接口"
            state={healthDown ? 'down' : health ? 'ok' : 'idle'}
            detail={health ? `v${health.version}` : '未连接'}
          />
          <StatusRow
            label="队列"
            state={brokerState}
            detail={
              health?.task_backend === 'local'
                ? '进程内'
                : health?.queue_depth != null
                  ? `排队 ${health.queue_depth}`
                  : '未知'
            }
          />
          <StatusRow
            label="Worker"
            state={health ? (health.worker_ok ? 'ok' : 'down') : 'idle'}
            detail={health ? (health.worker_ok ? '在线' : '未启动') : '未知'}
          />
        </div>
        <button
          type="button"
          onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
          className="mt-4 rounded-md border border-slate-700 px-3 py-2 text-xs text-slate-400 hover:bg-slate-800"
        >
          {theme === 'dark' ? '切换到浅色' : '切换到深色'}
        </button>
      </aside>

      <main className="flex-1 overflow-auto p-6">
        {tab === 'upload' && (
          <UploadPage
            onRegisterJob={(jobId) => registerJob({ kind: 'parse', jobId })}
            onCloseJob={() => closeSelectedOnChannel('upload')}
            onDeselect={() => deselectChannel('upload')}
            activeJob={selectedJobSnapshot(jobs, selection.upload)}
            selectedJobId={selection.upload}
            focusNonce={focusNonces.upload}
            onUploaded={refreshCounts}
            onWrite={goWrite}
          />
        )}
        {tab === 'library' && (
          <LibraryPage onWrite={goWrite} onChanged={refreshCounts} />
        )}
        {tab === 'write' && (
          <WritePage
            initialDocId={selectedDocId}
            onRegisterJob={(jobId) => registerJob({ kind: 'bid', jobId })}
            onCloseJob={() => closeSelectedOnChannel('write')}
            onDeselect={() => deselectChannel('write')}
            activeJob={selectedJobSnapshot(jobs, selection.write)}
            selectedJobId={selection.write}
            focusNonce={focusNonces.write}
            jobBridge={jobBridge}
            onWritten={refreshCounts}
          />
        )}
        {tab === 'history' && (
          <HistoryPage
            onChanged={refreshCounts}
            refreshNonce={historyRefreshNonce}
            jobBridge={jobBridge}
          />
        )}
      </main>

      <GlobalTaskBar jobs={jobs} onJumpTo={jumpToJob} onClose={closeJob} />
    </div>
  )
}