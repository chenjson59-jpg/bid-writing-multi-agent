import { useEffect, useRef, useState } from 'react'
import { bidFileUrl, createSupplement, describeError, getJob } from '../api'
import { getDesktop } from '../desktop'
import type { BidSummary } from '../types'
import { Badge, Banner, Button, Card, Modal, Spinner } from '../ui'

const STRATEGY_LABEL: Record<string, string> = {
  generate: '生成',
  fill_template: '填模板',
  placeholder: '占位',
}

function bidIdFromOutputDir(outputDir: string): string | null {
  if (!outputDir) return null
  const parts = outputDir.split(/[\\/]/)
  return parts[parts.length - 1] || null
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-100">{value}</p>
    </div>
  )
}

export function ResultView({
  bid,
  bidId,
  onSupplemented,
}: {
  bid: BidSummary
  bidId?: string
  onSupplemented?: () => void
}) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewText, setPreviewText] = useState('')
  const [loading, setLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [supplementState, setSupplementState] = useState<'idle' | 'running' | 'done' | 'failed'>(
    'idle',
  )
  const [supplementMsg, setSupplementMsg] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const id = bidId ?? bidIdFromOutputDir(bid.output_dir) ?? ''
  const desktop = getDesktop()

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current)
    },
    [],
  )

  function stopPolling() {
    if (timerRef.current !== null) window.clearInterval(timerRef.current)
    timerRef.current = null
  }

  function pollSupplement(jobId: string) {
    timerRef.current = window.setInterval(async () => {
      try {
        const job = await getJob(jobId)
        if (job.status === 'succeeded') {
          stopPolling()
          const newId = bidIdFromOutputDir(job.bid?.output_dir ?? '') ?? ''
          setSupplementState('done')
          setSupplementMsg(`补写完成，新产物 ${newId} 已生成，可在「标书历史」对比覆盖率`)
          onSupplemented?.()
        } else if (job.status === 'failed') {
          stopPolling()
          setSupplementState('failed')
          setSupplementMsg(job.error ?? '补写失败')
        }
      } catch (e) {
        stopPolling()
        setSupplementState('failed')
        setSupplementMsg(describeError(e))
      }
    }, 2000)
  }

  async function supplement() {
    if (!id) return
    setSupplementState('running')
    setSupplementMsg('已提交补写任务，正在等待新章节生成…')
    try {
      const accepted = await createSupplement(id)
      pollSupplement(accepted.job_id)
    } catch (e) {
      setSupplementState('failed')
      setSupplementMsg(describeError(e))
    }
  }

  async function openPreview() {
    setLoading(true)
    setPreviewError(null)
    try {
      const resp = await fetch(bidFileUrl(id, 'bid.md', false))
      if (!resp.ok) throw new Error(`预览失败（${resp.status}）`)
      setPreviewText(await resp.text())
      setPreviewOpen(true)
    } catch (e) {
      setPreviewError(describeError(e))
    } finally {
      setLoading(false)
    }
  }

  function download(name: string) {
    window.open(bidFileUrl(id, name, true), '_blank')
  }

  async function openPath(target: string) {
    if (!desktop) return
    const res = await desktop.openPath(target)
    if (!res.ok) alert(res.error ?? '打开失败')
  }

  async function showInFolder(target: string) {
    if (!desktop) return
    const res = await desktop.showItemInFolder(target)
    if (!res.ok) alert(res.error ?? '定位失败')
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">{bid.project_name || '（未命名项目）'}</h2>
            <p className="mt-1 text-sm text-slate-400">
              投标人：{bid.bidder || '未填写'} · {bid.doc_id}
            </p>
            {bid.supplement_of && (
              <p className="mt-1 text-xs text-sky-400">本产物为补写版，原产物：{bid.supplement_of}</p>
            )}
          </div>
          {bid.output_dir && (
            <p className="max-w-xs truncate text-xs text-slate-500" title={bid.output_dir}>
              {bid.output_dir}
            </p>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Metric label="章节 / 字数" value={`${bid.total_sections} / ${bid.total_words}`} />
        <Metric label="格式覆盖" value={`${bid.format_items_covered}/${bid.format_items_total}`} />
        <Metric label="评分覆盖" value={`${bid.evaluation_factors_covered}/${bid.evaluation_factors_total}`} />
        <Metric label="占位符" value={String(bid.placeholder_count)} />
      </div>

      {(bid.missing_profile_fields.length > 0 || bid.uncovered_factors.length > 0) && (
        <Card title="提示">
          <div className="space-y-2">
            {bid.missing_profile_fields.length > 0 && (
              <p className="text-sm text-amber-400">
                缺失资料：{bid.missing_profile_fields.join('、')}
              </p>
            )}
            {bid.uncovered_factors.length > 0 && (
              <p className="text-sm text-amber-400">
                未覆盖评分因素：{bid.uncovered_factors.join('、')}
              </p>
            )}
            {bid.warnings.map((w, i) => (
              <p key={i} className="text-sm text-rose-400">
                {w}
              </p>
            ))}
          </div>
        </Card>
      )}

      {bid.llm_usage && bid.llm_usage.calls > 0 && (
        <Card title="本次模型用量">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Metric label="调用次数" value={String(bid.llm_usage.calls)} />
            <Metric label="总 Token" value={String(bid.llm_usage.total_tokens)} />
            <Metric label="耗时" value={`${(bid.llm_usage.duration_ms / 1000).toFixed(1)} s`} />
            <Metric
              label="估算成本"
              value={
                bid.llm_usage.cost_estimated
                  ? `¥${bid.llm_usage.estimated_cost.toFixed(4)}`
                  : '未配置单价'
              }
            />
          </div>
          <p className="mt-3 text-xs text-slate-500">
            模型：{bid.llm_usage.models.join('、') || '未知'} · 输入 {bid.llm_usage.prompt_tokens} /
            输出 {bid.llm_usage.completion_tokens} token
            {!bid.llm_usage.cost_estimated &&
              '（可在 .env 配置 BID_WRITER_PRICE_INPUT_PER_1K / BID_WRITER_PRICE_OUTPUT_PER_1K 后重跑）'}
          </p>
        </Card>
      )}

      {bid.uncovered_factors.length > 0 && (
        <Card title="补写未覆盖因素">
          <p className="text-sm text-slate-400">
            针对未覆盖的 {bid.uncovered_factors.length} 项评分因素，追加章节并生成新产物（原产物保留，便于对比）。
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button onClick={supplement} disabled={!id || supplementState === 'running'}>
              {supplementState === 'running' ? <Spinner /> : '补写未覆盖因素'}
            </Button>
            {supplementState === 'done' && <Badge tone="success">已生成补写版</Badge>}
          </div>
          {supplementMsg && (
            <div className="mt-3">
              <Banner kind={supplementState === 'failed' ? 'error' : 'info'}>{supplementMsg}</Banner>
            </div>
          )}
        </Card>
      )}

      {bid.sections.length > 0 && (
        <Card title="章节明细">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-xs text-slate-500">
                  <th className="py-2 pr-4">章节</th>
                  <th className="py-2 pr-4">策略</th>
                  <th className="py-2 pr-4">修订前</th>
                  <th className="py-2 pr-4">修订后</th>
                  <th className="py-2 pr-4">修订次数</th>
                  <th className="py-2">字数</th>
                </tr>
              </thead>
              <tbody>
                {bid.sections.map((s) => (
                  <tr key={s.id} className="border-b border-slate-800/60">
                    <td className="py-2 pr-4 text-slate-200">{s.title}</td>
                    <td className="py-2 pr-4">
                      <Badge tone={s.strategy === 'placeholder' ? 'muted' : 'success'}>
                        {STRATEGY_LABEL[s.strategy] ?? s.strategy}
                      </Badge>
                    </td>
                    <td className="py-2 pr-4">{s.score_before}</td>
                    <td className="py-2 pr-4">{s.score_after}</td>
                    <td className="py-2 pr-4">{s.revisions}</td>
                    <td className="py-2">{s.word_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title="产物">
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={openPreview} disabled={loading || !id}>
            {loading ? <Spinner /> : '预览 Markdown'}
          </Button>
          <Button variant="secondary" onClick={() => download('bid.md')} disabled={!id}>
            下载 Markdown
          </Button>
          {bid.docx_path && (
            <Button variant="secondary" onClick={() => download('bid.docx')} disabled={!id}>
              下载 Word
            </Button>
          )}
          <Button variant="secondary" onClick={() => download('report.json')} disabled={!id}>
            下载覆盖报告
          </Button>
          {desktop && bid.docx_path && (
            <Button variant="secondary" onClick={() => openPath(bid.docx_path)}>
              打开 Word
            </Button>
          )}
          {desktop && bid.output_dir && (
            <Button variant="secondary" onClick={() => showInFolder(bid.output_dir)}>
              打开所在文件夹
            </Button>
          )}
        </div>
        {!bid.docx_path && (
          <p className="mt-3 text-sm text-slate-500">Word 导出不可用（未导出或导出失败）。</p>
        )}
      </Card>

      <p className="text-xs text-slate-600">草稿由模型生成，仅供投标人参考，提交前请逐项核对。</p>

      <Modal open={previewOpen} title="Markdown 预览" onClose={() => setPreviewOpen(false)}>
        {previewError && <Banner kind="error">{previewError}</Banner>}
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-xs text-slate-300">
          {previewText}
        </pre>
      </Modal>
    </div>
  )
}
