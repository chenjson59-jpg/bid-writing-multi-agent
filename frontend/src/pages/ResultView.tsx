import { useState } from 'react'
import { bidFileUrl, describeError } from '../api'
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

export function ResultView({ bid, bidId }: { bid: BidSummary; bidId?: string }) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewText, setPreviewText] = useState('')
  const [loading, setLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const id = bidId ?? bidIdFromOutputDir(bid.output_dir) ?? ''
  const desktop = getDesktop()

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
