import { useCallback, useEffect, useState } from 'react'
import { listBids, listDocuments } from './api'
import { HistoryPage } from './pages/HistoryPage'
import { LibraryPage } from './pages/LibraryPage'
import { UploadPage } from './pages/UploadPage'
import { WritePage } from './pages/WritePage'

type Tab = 'upload' | 'library' | 'write' | 'history'
type Theme = 'dark' | 'light'

function loadTheme(): Theme {
  try {
    return localStorage.getItem('theme') === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>('upload')
  const [theme, setTheme] = useState<Theme>(loadTheme)
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)
  const [runningParse, setRunningParse] = useState(false)
  const [runningBid, setRunningBid] = useState(false)
  const [docCount, setDocCount] = useState(0)
  const [bidCount, setBidCount] = useState(0)

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
    { id: 'history', label: '标书历史', running: false, count: bidCount },
  ]

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
        {tab === 'library' && <LibraryPage onWrite={goWrite} />}
        {tab === 'write' && (
          <WritePage
            initialDocId={selectedDocId}
            onRunningChange={setRunningBid}
            onWritten={refreshCounts}
          />
        )}
        {tab === 'history' && <HistoryPage />}
      </main>
    </div>
  )
}
