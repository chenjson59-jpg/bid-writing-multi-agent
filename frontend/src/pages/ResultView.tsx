import { useEffect, useRef, useState } from 'react'
import {
  bidFileUrl,
  createSupplement,
  describeError,
  getJob,
  getBid,
  getSection,
  listSections,
  rewriteSections,
  saveSection,
  sectionFileUrl,
} from '../api'
import { getDesktop } from '../desktop'
import type { BidSummary, SectionDetail, SectionInfo } from '../types'
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
  onChanged,
}: {
  bid: BidSummary
  bidId?: string
  onSupplemented?: () => void
  onChanged?: () => void
}) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewText, setPreviewText] = useState('')
  const [loading, setLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [supplementState, setSupplementState] = useState<'idle' | 'running' | 'done' | 'failed'>(
    'idle',
  )
  const [supplementMsg, setSupplementMsg] = useState<string | null>(null)

  // 章节级编辑（P1-2）：本地覆盖最新摘要，避免依赖父组件重新拉取
  const [override, setOverride] = useState<BidSummary | null>(null)
  const [sections, setSections] = useState<SectionInfo[]>([])
  const [sectionError, setSectionError] = useState<string | null>(null)
  const [busySection, setBusySection] = useState<string | null>(null)
  const [rewriteState, setRewriteState] = useState<'idle' | 'running' | 'done' | 'failed'>('idle')
  const [rewriteMsg, setRewriteMsg] = useState<string | null>(null)

  const [editing, setEditing] = useState<SectionDetail | null>(null)
  const [editText, setEditText] = useState('')
  const [editLocked, setEditLocked] = useState(false)
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  const timerRef = useRef<number | null>(null)
  const rewriteTimerRef = useRef<number | null>(null)
  const id = bidId ?? bidIdFromOutputDir(bid.output_dir) ?? ''
  const data = override ?? bid
  const desktop = getDesktop()

  useEffect(() => setOverride(null), [bid])

  useEffect(() => {
    if (!id) return
    let cancelled = false
    listSections(id)
      .then((items) => {
        if (!cancelled) setSections(items)
      })
      .catch((e) => {
        // 旧版产物没有章节落盘文件时不影响其余功能
        if (!cancelled) setSectionError(describeError(e))
      })
    return () => {
      cancelled = true
    }
  }, [id])

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current)
      if (rewriteTimerRef.current !== null) window.clearInterval(rewriteTimerRef.current)
    },
    [],
  )

  function stopPolling() {
    if (timerRef.current !== null) window.clearInterval(timerRef.current)
    timerRef.current = null
  }

  function stopRewritePolling() {
    if (rewriteTimerRef.current !== null) window.clearInterval(rewriteTimerRef.current)
    rewriteTimerRef.current = null
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

  async function refreshSections() {
    if (!id) return
    try {
      setSections(await listSections(id))
      setSectionError(null)
    } catch (e) {
      setSectionError(describeError(e))
    }
  }

  async function refreshSummary() {
    if (!id) return
    try {
      const item = await getBid(id)
      setOverride(item.summary)
    } catch (e) {
      setSectionError(describeError(e))
    }
    await refreshSections()
    onChanged?.()
  }

  function pollRewrite(jobId: string) {
    rewriteTimerRef.current = window.setInterval(async () => {
      try {
        const job = await getJob(jobId)
        if (job.status === 'succeeded') {
          stopRewritePolling()
          setRewriteState('done')
          setRewriteMsg('章节重写完成，产物已原地更新')
          await refreshSummary()
        } else if (job.status === 'failed') {
          stopRewritePolling()
          setRewriteState('failed')
          setRewriteMsg(job.error ?? '章节重写失败')
        }
      } catch (e) {
        stopRewritePolling()
        setRewriteState('failed')
        setRewriteMsg(describeError(e))
      }
    }, 2000)
  }

  async function rewrite(sectionIds: string[] | null) {
    if (!id) return
    if (sectionIds === null && !window.confirm('重写全部未锁定章节？锁定章节会保持不变。')) return
    setRewriteState('running')
    setRewriteMsg(sectionIds ? '已提交单章重写任务…' : '已提交批量重写任务…')
    try {
      const accepted = await rewriteSections(id, sectionIds)
      pollRewrite(accepted.job_id)
    } catch (e) {
      setRewriteState('failed')
      setRewriteMsg(describeError(e))
    }
  }

  async function toggleLock(sectionId: string, locked: boolean) {
    if (!id) return
    setBusySection(sectionId)
    try {
      const summary = await saveSection(id, sectionId, { locked: !locked })
      setOverride(summary)
      await refreshSections()
      onChanged?.()
    } catch (e) {
      setSectionError(describeError(e))
    } finally {
      setBusySection(null)
    }
  }

  async function openEditor(sectionId: string) {
    if (!id) return
    setBusySection(sectionId)
    setEditError(null)
    try {
      const detail = await getSection(id, sectionId)
      setEditing(detail)
      setEditText(detail.markdown)
      setEditLocked(detail.locked)
    } catch (e) {
      setSectionError(describeError(e))
    } finally {
      setBusySection(null)
    }
  }

  async function saveEditor() {
    if (!id || !editing) return
    setEditSaving(true)
    setEditError(null)
    try {
      const summary = await saveSection(id, editing.id, {
        markdown: editText,
        locked: editLocked,
      })
      setOverride(summary)
      setEditing(null)
      await refreshSections()
      onChanged?.()
    } catch (e) {
      setEditError(describeError(e))
    } finally {
      setEditSaving(false)
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

  const lockMap = new Map(sections.map((s) => [s.id, s.locked]))
  const lockedCount = sections.filter((s) => s.locked).length

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold text-slate-100">{data.project_name || '（未命名项目）'}</h2>
            <p className="mt-1 text-sm text-slate-400">
              投标人：{data.bidder || '未填写'} · {data.doc_id}
            </p>
            {data.supplement_of && (
              <p className="mt-1 text-xs text-sky-400">本产物为补写版，原产物：{data.supplement_of}</p>
            )}
          </div>
          {data.output_dir && (
            <p className="max-w-xs truncate text-xs text-slate-500" title={data.output_dir}>
              {data.output_dir}
            </p>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Metric label="章节 / 字数" value={`${data.total_sections} / ${data.total_words}`} />
        <Metric label="格式覆盖" value={`${data.format_items_covered}/${data.format_items_total}`} />
        <Metric label="评分覆盖" value={`${data.evaluation_factors_covered}/${data.evaluation_factors_total}`} />
        <Metric label="占位符" value={String(data.placeholder_count)} />
      </div>

      {(data.missing_profile_fields.length > 0 || data.uncovered_factors.length > 0) && (
        <Card title="提示">
          <div className="space-y-2">
            {data.missing_profile_fields.length > 0 && (
              <p className="text-sm text-amber-400">
                缺失资料：{data.missing_profile_fields.join('、')}
              </p>
            )}
            {data.uncovered_factors.length > 0 && (
              <p className="text-sm text-amber-400">
                未覆盖评分因素：{data.uncovered_factors.join('、')}
              </p>
            )}
            {data.warnings.map((w, i) => (
              <p key={i} className="text-sm text-rose-400">
                {w}
              </p>
            ))}
          </div>
        </Card>
      )}

      {data.llm_usage && data.llm_usage.calls > 0 && (
        <Card title="本次模型用量">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Metric label="调用次数" value={String(data.llm_usage.calls)} />
            <Metric label="总 Token" value={String(data.llm_usage.total_tokens)} />
            <Metric label="耗时" value={`${(data.llm_usage.duration_ms / 1000).toFixed(1)} s`} />
            <Metric
              label="估算成本"
              value={
                data.llm_usage.cost_estimated
                  ? `¥${data.llm_usage.estimated_cost.toFixed(4)}`
                  : '未配置单价'
              }
            />
          </div>
          <p className="mt-3 text-xs text-slate-500">
            模型：{data.llm_usage.models.join('、') || '未知'} · 输入 {data.llm_usage.prompt_tokens} /
            输出 {data.llm_usage.completion_tokens} token
            {!data.llm_usage.cost_estimated &&
              '（可在 .env 配置 BID_WRITER_PRICE_INPUT_PER_1K / BID_WRITER_PRICE_OUTPUT_PER_1K 后重跑）'}
          </p>
        </Card>
      )}

      {data.uncovered_factors.length > 0 && (
        <Card title="补写未覆盖因素">
          <p className="text-sm text-slate-400">
            针对未覆盖的 {data.uncovered_factors.length} 项评分因素，追加章节并生成新产物（原产物保留，便于对比）。
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

      {data.sections.length > 0 && (
        <Card title="章节明细">
          <p className="text-xs text-slate-500">
            可编辑单章 Markdown、单独重写或锁定；锁定章节不会被批量重写覆盖，保存后会自动重新合并
            Markdown、Word 与覆盖报告。
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => rewrite(null)}
              disabled={!id || rewriteState === 'running'}
            >
              {rewriteState === 'running' ? <Spinner /> : '重写全部未锁定章节'}
            </Button>
            <Button variant="ghost" onClick={refreshSections} disabled={!id}>
              刷新章节
            </Button>
            {lockedCount > 0 && <Badge tone="muted">已锁定 {lockedCount} 章</Badge>}
          </div>
          {(sectionError || rewriteMsg) && (
            <div className="mt-3 space-y-2">
              {sectionError && <Banner kind="error">{sectionError}</Banner>}
              {rewriteMsg && (
                <Banner kind={rewriteState === 'failed' ? 'error' : 'info'}>{rewriteMsg}</Banner>
              )}
            </div>
          )}
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-800 text-xs text-slate-500">
                  <th className="py-2 pr-4">章节</th>
                  <th className="py-2 pr-4">策略</th>
                  <th className="py-2 pr-4">修订前</th>
                  <th className="py-2 pr-4">修订后</th>
                  <th className="py-2 pr-4">字数</th>
                  <th className="py-2 pr-4">状态</th>
                  <th className="py-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {data.sections.map((s) => {
                  const locked = lockMap.get(s.id) ?? false
                  const busy = busySection === s.id
                  return (
                    <tr key={s.id} className="border-b border-slate-800/60">
                      <td className="py-2 pr-4 text-slate-200">{s.title}</td>
                      <td className="py-2 pr-4">
                        <Badge tone={s.strategy === 'placeholder' ? 'muted' : 'success'}>
                          {STRATEGY_LABEL[s.strategy] ?? s.strategy}
                        </Badge>
                      </td>
                      <td className="py-2 pr-4">{s.score_before}</td>
                      <td className="py-2 pr-4">{s.score_after}</td>
                      <td className="py-2 pr-4">{s.word_count}</td>
                      <td className="py-2 pr-4">
                        {locked ? <Badge tone="muted">已锁定</Badge> : <span className="text-slate-500">可重写</span>}
                      </td>
                      <td className="py-2">
                        <div className="flex flex-wrap items-center gap-1">
                          <Button
                            variant="ghost"
                            onClick={() => openEditor(s.id)}
                            disabled={busy || !id}
                          >
                            编辑
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() => rewrite([s.id])}
                            disabled={busy || locked || rewriteState === 'running' || !id}
                          >
                            重写
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() => toggleLock(s.id, locked)}
                            disabled={busy || !id}
                          >
                            {locked ? '解锁' : '锁定'}
                          </Button>
                          {id && (
                            <a
                              className="px-2 py-1 text-xs text-slate-400 hover:text-slate-200"
                              href={sectionFileUrl(id, s.id)}
                              target="_blank"
                              rel="noreferrer"
                            >
                              导出
                            </a>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
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
          {data.docx_path && (
            <Button variant="secondary" onClick={() => download('bid.docx')} disabled={!id}>
              下载 Word
            </Button>
          )}
          <Button variant="secondary" onClick={() => download('report.json')} disabled={!id}>
            下载覆盖报告
          </Button>
          {desktop && data.docx_path && (
            <Button variant="secondary" onClick={() => openPath(data.docx_path)}>
              打开 Word
            </Button>
          )}
          {desktop && data.output_dir && (
            <Button variant="secondary" onClick={() => showInFolder(data.output_dir)}>
              打开所在文件夹
            </Button>
          )}
        </div>
        {!data.docx_path && (
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

      <Modal
        open={editing !== null}
        title={`编辑章节：${editing?.title ?? ''}`}
        onClose={() => setEditing(null)}
      >
        <div className="space-y-3">
          {editError && <Banner kind="error">{editError}</Banner>}
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={editLocked}
              onChange={(e) => setEditLocked(e.target.checked)}
            />
            锁定本章（批量重写时保持不变）
          </label>
          <textarea
            className="h-[50vh] w-full rounded-md border border-slate-700 bg-slate-950 p-3 font-mono text-xs text-slate-200"
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(null)} disabled={editSaving}>
              取消
            </Button>
            <Button onClick={saveEditor} disabled={editSaving}>
              {editSaving ? <Spinner /> : '保存并刷新产物'}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
