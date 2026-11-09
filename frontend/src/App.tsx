import { useCallback, useEffect, useRef, useState } from 'react'
import { getHealth, getJob, listBids, listDocuments } from './api'
import { HistoryPage } from './pages/HistoryPage'
import { LibraryPage } from './pages/LibraryPage'
import { UploadPage } from './pages/UploadPage'
import { WritePage } from './pages/WritePage'
import {
  decidePollOutcome,
  isRunning,
  jobEquals,
  kindLabel,
  nextHistoryRefreshNonce,
  planJobJump,
  statusLabel,
  type JobKind,
  type Tab,
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

// 全局任务进度条：fixed 贴底，任何页面都能看到，切 tab 不会丢
function GlobalTaskBar({
  parseJob,
  bidJob,
  onJumpTo,
}: {
  parseJob: JobView | null
  bidJob: JobView | null
  onJumpTo: (kind: JobKind) => void
}) {
  // 显式带上 kind 槽位，保证「查看」回调类型安全（activeJobs 只有 parse/bid 两槽）
  const slots: { kind: JobKind; job: JobView | null }[] = [
    { kind: 'parse', job: parseJob },
    { kind: 'bid', job: bidJob },
  ]
  const active = slots.filter((s): s is { kind: JobKind; job: JobView } => isRunning(s.job))
  if (active.length === 0) return null

  return (
    <div className="pointer-events-none fixed bottom-0 left-0 right-0 z-50 flex justify-center p-3">
      <div className="pointer-events-auto w-[min(720px,92vw)] rounded-lg border border-sky-600/40 bg-slate-900/95 shadow-xl shadow-sky-900/30 backdrop-blur">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2 text-xs">
          <span className="flex items-center gap-1 text-sky-400">
            <span className="h-2 w-2 animate-pulse rounded-full bg-sky-400" />
            正在执行 {active.length} 个任务
          </span>
        </div>
        <ul>
          {active.map(({ kind, job }) => (
            <li
              key={job.job_id}
              className="flex items-center gap-3 border-b border-slate-800/70 px-4 py-2 text-sm last:border-b-0"
            >
              <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                {kindLabel(job.kind)}
              </span>
              <span className="flex-1 truncate text-slate-300">
                {job.source_name || '任务 ' + job.job_id.slice(0, 8)}
              </span>
              <span
                className={
                  job.status === 'running'
                    ? 'text-sky-400'
                    : job.status === 'pending'
                      ? 'text-amber-400'
                      : job.status === 'succeeded'
                        ? 'text-emerald-400'
                        : 'text-rose-400'
                }
              >
                {statusLabel(job.status)}
              </span>
              {job.status === 'running' && job.progress.length > 0 && (
                <span className="max-w-[220px] truncate text-xs text-slate-500">
                  {job.progress[job.progress.length - 1]}
                </span>
              )}
              <button
                type="button"
                className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200"
                onClick={() => onJumpTo(kind)}
              >
                查看
              </button>
            </li>
          ))}
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

  // ---------- 全局任务状态：App 永远存活，切 tab 不卸载 ----------
  const [activeJobs, setActiveJobs] = useState<Record<JobKind, JobView | null>>({
    parse: null,
    bid: null,
  })
  // 每种任务对应的后端 job_id（存在 ref 里避免闭包陈旧）
  const activeJobIdRef = useRef<Record<JobKind, string | null>>({ parse: null, bid: null })
  // T-031：任务选择态上移 App。页面不再自持 jobId，重挂后凭此恢复任务视图（单槽/kind）
  const [selectedJobIds, setSelectedJobIds] = useState<Record<JobKind, string | null>>({
    parse: null,
    bid: null,
  })
  // 「查看」聚焦信号：每次点击自增，页面 effect 据此滚动/高亮任务卡片
  const [focusNonces, setFocusNonces] = useState<Record<JobKind, number>>({ parse: 0, bid: 0 })
  // T-030：终态事件驱动的历史页刷新信号，仅在非终态→终态迁移时自增（无任务不空转）
  const [historyRefreshNonce, setHistoryRefreshNonce] = useState(0)
  // poller 上一次观测值，用于终态迁移判定；放 ref 而非 setState updater，避免 StrictMode 双调用
  const pollPrevRef = useRef<Record<JobKind, JobView | null>>({ parse: null, bid: null })

  // 注册一个新的活跃任务（页面触发任务后调这个）
  const registerJob = useCallback((kind: JobKind, jobId: string | null) => {
    activeJobIdRef.current[kind] = jobId
    // T-030：复位迁移观测，保证新任务的终态迁移一定能被识别
    pollPrevRef.current[kind] = null
    // 选择态立即置位：首帧 poll 未返回时页面也能展示「提交中」
    setSelectedJobIds((prev) => ({ ...prev, [kind]: jobId }))
    if (!jobId) {
      // 取消/出错/返回修改参数/显式复位：立即清掉活跃任务与选择态，页面回表单
      setActiveJobs((prev) => ({ ...prev, [kind]: null }))
    }
  }, [])

  // 统一 poller：每秒 GET 每个活跃 jobId，更新 activeJobs
  useEffect(() => {
    const tick = async () => {
      const kinds: JobKind[] = ['parse', 'bid']
      for (const kind of kinds) {
        const jobId = activeJobIdRef.current[kind]
        if (!jobId) continue
        try {
          const job = await getJob(jobId)
          // T-030：终态即停轮询、快照留存；仅在非终态→终态迁移时通知历史页刷新
          const outcome = decidePollOutcome(pollPrevRef.current[kind], job)
          pollPrevRef.current[kind] = job
          setActiveJobs((prev) => {
            if (jobEquals(prev[kind], job)) return prev
            return { ...prev, [kind]: job }
          })
          if (outcome.stopPolling) {
            // 终态后不再请求该 job；activeJobs/selectedJobIds 保留，清理只允许来自
            // registerJob(kind, null) 的用户显式复位，定时器不得再改变页面状态
            activeJobIdRef.current[kind] = null
          }
          if (outcome.terminalTransition) {
            setHistoryRefreshNonce((n) => nextHistoryRefreshNonce(n, true))
          }
        } catch {
          // poll 失败（网络抖动）静默跳过，等下一轮
        }
      }
    }
    const timer = window.setInterval(tick, 1500)
    return () => window.clearInterval(timer)
  }, [])

  // 派生：运行中 → 左侧 nav 蓝点
  const runningParse = isRunning(activeJobs.parse)
  const runningBid = isRunning(activeJobs.bid)

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

  // 底部任务栏「查看」：切到目标 tab（若需要）并通知页面滚动/高亮任务卡片
  const jumpToJob = useCallback(
    (kind: JobKind) => {
      const decision = planJobJump(kind, tab)
      setTab(decision.tab)
      setFocusNonces((prev) => ({ ...prev, [kind]: prev[kind] + 1 }))
    },
    [tab],
  )

  // 页面底部 padding，防止被 GlobalTaskBar 遮住
  const hasActiveTask = runningParse || runningBid
  const bottomPad = hasActiveTask ? 'pb-24' : ''

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
            onRegisterJob={(jobId) => registerJob('parse', jobId)}
            activeJob={activeJobs.parse}
            selectedJobId={selectedJobIds.parse}
            focusNonce={focusNonces.parse}
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
            onRegisterJob={(jobId) => registerJob('bid', jobId)}
            activeJob={activeJobs.bid}
            selectedJobId={selectedJobIds.bid}
            focusNonce={focusNonces.bid}
            onWritten={refreshCounts}
          />
        )}
        {tab === 'history' && (
          <HistoryPage onChanged={refreshCounts} refreshNonce={historyRefreshNonce} />
        )}
      </main>

      <GlobalTaskBar parseJob={activeJobs.parse} bidJob={activeJobs.bid} onJumpTo={jumpToJob} />
    </div>
  )
}
