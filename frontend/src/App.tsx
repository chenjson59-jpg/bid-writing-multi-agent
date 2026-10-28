import { useCallback, useEffect, useState } from 'react'
import { getHealth, listBids, listDocuments } from './api'
import { HistoryPage } from './pages/HistoryPage'
import { LibraryPage } from './pages/LibraryPage'
import { UploadPage } from './pages/UploadPage'
import { WritePage } from './pages/WritePage'
import type { HealthInfo } from './types'

type Tab = 'upload' | 'library' | 'write' | 'history'
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

export default function App() {
  const [tab, setTab] = useState<Tab>('upload')
  const [theme, setTheme] = useState<Theme>(loadTheme)
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)
  const [runningParse, setRunningParse] = useState(false)
  const [runningBid, setRunningBid] = useState(false)
  const [docCount, setDocCount] = useState(0)
  const [bidCount, setBidCount] = useState(0)
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [healthDown, setHealthDown] = useState(false)

  // 每 5 秒探测一次：任一组件掉线在顶栏直接可见，避免用户操作后才报错
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

  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
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
            onRunningChange={setRunningParse}
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
            onRunningChange={setRunningBid}
            onWritten={refreshCounts}
          />
        )}
        {tab === 'history' && <HistoryPage onChanged={refreshCounts} />}
      </main>
    </div>
  )
}
