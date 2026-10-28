import { useEffect, useState } from 'react'
import {
  cancelJob,
  deleteBid,
  describeError,
  listBids,
  listTasks,
  retryJob,
} from '../api'
import type { BidHistoryItem, TaskHistoryItem } from '../types'
import { Badge, Banner, Button, Card, EmptyState, Spinner } from '../ui'
import { ResultView } from './ResultView'

const KIND_LABEL: Record<string, string> = {
  parse: '解析',
  bid: '编写',
  supplement: '补写',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '排队',
  running: '运行中',
  succeeded: '成功',
  failed: '失败',
}

function statusTone(status: string): 'success' | 'warn' | 'muted' {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'warn'
  return 'muted'
}

function taskDetail(task: TaskHistoryItem): string {
  const summary = (task.summary ?? {}) as Record<string, unknown>
  const parts: string[] = []
  if (summary.project_name) parts.push(String(summary.project_name))
  if (summary.pages != null) parts.push(`${summary.pages} 页`)
  if (summary.total_sections != null) parts.push(`${summary.total_sections} 章`)
  if (task.error) parts.push(task.error)
  return parts.join(' · ')
}

export function HistoryPage({ onChanged }: { onChanged?: () => void }) {
  const [view, setView] = useState<'bids' | 'tasks'>('bids')
  const [items, setItems] = useState<BidHistoryItem[]>([])
  const [tasks, setTasks] = useState<TaskHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      // 任务历史依赖数据库，库不可用时后端返回空列表，不影响产物展示
      const [bids, history] = await Promise.all([listBids(), listTasks()])
      setItems(bids)
      setTasks(history)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function removeBid(bidId: string) {
    if (!window.confirm(`确认删除产物 ${bidId}？目录会连同 Word 与报告一起删除。`)) return
    setBusy(bidId)
    setNotice(null)
    try {
      await deleteBid(bidId)
      setNotice(`已删除产物 ${bidId}`)
      await load()
      onChanged?.()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(null)
    }
  }

  async function cancel(jobId: string) {
    setBusy(jobId)
    setNotice(null)
    try {
      await cancelJob(jobId)
      setNotice(`已取消任务 ${jobId}`)
      await load()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(null)
    }
  }

  async function retry(jobId: string) {
    setBusy(jobId)
    setNotice(null)
    try {
      const accepted = await retryJob(jobId)
      setNotice(`已重新提交为 ${accepted.job_id}，可在任务记录中查看进度`)
      await load()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(null)
    }
  }

  const tabs = [
    { id: 'bids' as const, label: `标书产物（${items.length}）` },
    { id: 'tasks' as const, label: `任务记录（${tasks.length}）` },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-slate-100">任务与产物</h2>
        <div className="flex items-center gap-2">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setView(t.id)}
              className={`rounded-md px-3 py-2 text-sm ${
                view === t.id
                  ? 'bg-slate-800 text-slate-100'
                  : 'text-slate-400 hover:bg-slate-900'
              }`}
            >
              {t.label}
            </button>
          ))}
          <Button variant="ghost" onClick={load} disabled={loading}>
            刷新
          </Button>
        </div>
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {notice && <Banner kind="info">{notice}</Banner>}

      {loading ? (
        <Spinner />
      ) : view === 'bids' ? (
        items.length === 0 ? (
          <EmptyState title="暂无标书产物" hint="完成一次编写后，产物会出现在这里" />
        ) : (
          <div className="space-y-2">
            {items.map((item) => {
              const s = item.summary
              const expanded = expandedId === item.bid_id
              return (
                <Card key={item.bid_id}>
                  <div className="flex items-center justify-between gap-3">
                    <button
                      type="button"
                      className="flex min-w-0 flex-1 items-center justify-between gap-3 text-left"
                      onClick={() => setExpandedId(expanded ? null : item.bid_id)}
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-slate-200">
                          {s.project_name || '（未命名项目）'} · {s.bidder || '未填写投标人'}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          {item.bid_id} · {item.generated_at ?? ''}
                          {s.supplement_of ? ` · 补写自 ${s.supplement_of}` : ''}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-4 text-xs text-slate-400">
                        <span>章节 {s.total_sections}</span>
                        <span>字数 {s.total_words}</span>
                        <span>
                          评分覆盖 {s.evaluation_factors_covered}/{s.evaluation_factors_total}
                        </span>
                        <span>占位 {s.placeholder_count}</span>
                        <span className="text-sky-400">{expanded ? '收起' : '展开'}</span>
                      </div>
                    </button>
                    <Button
                      variant="ghost"
                      onClick={() => removeBid(item.bid_id)}
                      disabled={busy === item.bid_id}
                    >
                      删除
                    </Button>
                  </div>
                  {expanded && (
                    <div className="mt-4">
                      <ResultView bid={s} bidId={item.bid_id} onSupplemented={load} />
                    </div>
                  )}
                </Card>
              )
            })}
          </div>
        )
      ) : tasks.length === 0 ? (
        <EmptyState
          title="暂无任务记录"
          hint="任务记录依赖 PostgreSQL；未配置数据库时此处恒为空"
        />
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            <Card key={task.job_id}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge>{KIND_LABEL[task.kind] ?? task.kind}</Badge>
                    <Badge tone={statusTone(task.status)}>
                      {STATUS_LABEL[task.status] ?? task.status}
                    </Badge>
                    <p className="truncate text-sm text-slate-200">
                      {task.source_name || task.doc_id || task.job_id}
                    </p>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {task.job_id} · {task.created_at ?? ''}
                  </p>
                  {taskDetail(task) && (
                    <p className="mt-1 truncate text-xs text-slate-400">{taskDetail(task)}</p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {(task.status === 'pending' || task.status === 'running') && (
                    <Button
                      variant="secondary"
                      onClick={() => cancel(task.job_id)}
                      disabled={busy === task.job_id}
                    >
                      取消
                    </Button>
                  )}
                  {task.status === 'failed' && (
                    <Button
                      onClick={() => retry(task.job_id)}
                      disabled={busy === task.job_id}
                    >
                      重试
                    </Button>
                  )}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
