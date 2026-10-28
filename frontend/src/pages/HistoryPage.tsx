import { useEffect, useState } from 'react'
import { describeError, listBids } from '../api'
import type { BidHistoryItem } from '../types'
import { Banner, Card, EmptyState, Spinner } from '../ui'
import { ResultView } from './ResultView'

export function HistoryPage() {
  const [items, setItems] = useState<BidHistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      setItems(await listBids())
    } catch (e) {
      setError(describeError(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-100">标书历史</h2>

      {error && <Banner kind="error">{error}</Banner>}

      {loading ? (
        <Spinner />
      ) : items.length === 0 ? (
        <EmptyState title="暂无标书历史" hint="完成一次编写后，产物会出现在这里" />
      ) : (
        <div className="space-y-2">
          {items.map((item) => {
            const s = item.summary
            const expanded = expandedId === item.bid_id
            return (
              <Card key={item.bid_id}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-3 text-left"
                  onClick={() => setExpandedId(expanded ? null : item.bid_id)}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-200">
                      {s.project_name || '（未命名项目）'} · {s.bidder || '未填写投标人'}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {item.bid_id} · {item.generated_at ?? ''}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-4 text-xs text-slate-400">
                    <span>章节 {s.total_sections}</span>
                    <span>字数 {s.total_words}</span>
                    <span>评分覆盖 {s.evaluation_factors_covered}/{s.evaluation_factors_total}</span>
                    <span>占位 {s.placeholder_count}</span>
                    <span className="text-sky-400">{expanded ? '收起' : '展开'}</span>
                  </div>
                </button>
                {expanded && (
                  <div className="mt-4">
                    <ResultView bid={s} bidId={item.bid_id} />
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
