import { useCallback, useEffect, useRef, useState } from 'react'
import {
  bidFileUrl,
  createSupplement,
  describeError,
  getBid,
  getSection,
  listSections,
  rewriteSections,
  saveSection,
  sectionFileUrl,
} from '../api'
import { getDesktop } from '../desktop'
import type { BidSummary, JobView, SectionDetail, SectionInfo } from '../types'
import { deriveSubTaskState, type JobBridge } from '../lib/task_state'
import {
  coverageLayers,
  isRetrievalDegraded,
  ruleErrorSections,
} from '../lib/bid_quality'
import { Badge, Banner, Button, Card, Modal, Spinner } from '../ui'
import { PlaceholderWorkbench, type PlaceholderWorkbenchHandle } from './PlaceholderWorkbench'
import { isPlaceholderMarker, splitByPlaceholder } from '../lib/placeholder_workbench'

const STRATEGY_LABEL: Record<string, string> = {
  generate: '生成',
  llm_fill: 'LLM 填写',
  // 旧产物中的遗留策略名，后端读取时已归一化为 llm_fill，这里保留兜底展示
  fill_template: 'LLM 填写',
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
  jobBridge,
}: {
  bid: BidSummary
  bidId?: string
  onSupplemented?: () => void
  onChanged?: () => void
  /** T-034：补写/重写任务登记到全局 poller 的桥接；未接入时仅保留触发按钮本地态 */
  jobBridge?: JobBridge
}) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewText, setPreviewText] = useState('')
  const [loading, setLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  // T-034：补写/重写不再持有私有轮询，只登记 jobId；状态与文案由全局快照派生
  const [supplementJobId, setSupplementJobId] = useState<string | null>(null)
  const [supplementCreateError, setSupplementCreateError] = useState<string | null>(null)
  const [rewriteJobId, setRewriteJobId] = useState<string | null>(null)
  const [rewriteCreateError, setRewriteCreateError] = useState<string | null>(null)
  const [rewriteSubmitText, setRewriteSubmitText] = useState<string | null>(null)

  // 章节级编辑（P1-2）：本地覆盖最新摘要，避免依赖父组件重新拉取
  const [override, setOverride] = useState<BidSummary | null>(null)
  const [sections, setSections] = useState<SectionInfo[]>([])
  const [sectionError, setSectionError] = useState<string | null>(null)
  const [busySection, setBusySection] = useState<string | null>(null)

  // 本实例登记过的全部跟踪 jobId，卸载时统一撤销终态回调（任务本身继续全局轮询）
  const trackedJobIdsRef = useRef<string[]>([])
  const bridgeRef = useRef(jobBridge)
  bridgeRef.current = jobBridge
  // T-039：章节弹窗点击占位标记后展开工作台并聚焦对应输入
  const workbenchRef = useRef<PlaceholderWorkbenchHandle>(null)

  const [editing, setEditing] = useState<SectionDetail | null>(null)
  const [editText, setEditText] = useState('')
  const [editLocked, setEditLocked] = useState(false)
  const [editSaving, setEditSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

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

  // T-034：卸载时撤销本实例登记的全部终态回调；任务仍在全局集合中继续轮询，
  // 重挂后凭 mount 拉取（listSections）与全局终态事件恢复数据，不依赖旧回调补账
  useEffect(
    () => () => {
      trackedJobIdsRef.current.forEach((jobId) => bridgeRef.current?.revoke(jobId))
    },
    [],
  )

  // 全局快照派生：无 jobId 为 idle；条目被显式关闭为 closed；快照缺失或 pending/running 为 running
  const supplementExists = supplementJobId ? (jobBridge?.has(supplementJobId) ?? true) : true
  const rewriteExists = rewriteJobId ? (jobBridge?.has(rewriteJobId) ?? true) : true
  const supplementJob = supplementJobId ? jobBridge?.find(supplementJobId) ?? null : null
  const rewriteJob = rewriteJobId ? jobBridge?.find(rewriteJobId) ?? null : null
  const supplementState = deriveSubTaskState(supplementJobId, supplementExists, supplementJob)
  const rewriteState = deriveSubTaskState(rewriteJobId, rewriteExists, rewriteJob)

  // 终态文案同样由全局快照派生，回调不做任何 setState，杜绝对已卸载组件 setState
  const supplementMsg = supplementJobId
    ? supplementState === 'closed'
      ? '补写任务已从任务栏关闭，可重新发起补写'
      : supplementState === 'done'
        ? `补写完成，新产物 ${bidIdFromOutputDir(supplementJob?.bid?.output_dir ?? '') ?? ''} 已生成，可在「标书历史」对比覆盖率`
        : supplementState === 'failed'
          ? (supplementJob?.error ?? '补写失败')
          : '已提交补写任务，正在等待新章节生成…'
    : supplementCreateError

  const rewriteMsg = rewriteJobId
    ? rewriteState === 'closed'
      ? '重写任务已从任务栏关闭，可重新发起重写'
      : rewriteState === 'done'
        ? '章节重写完成，产物已原地更新'
        : rewriteState === 'failed'
          ? (rewriteJob?.error ?? '章节重写失败')
          : (rewriteSubmitText ?? '已提交章节重写任务…')
    : rewriteCreateError

  // 补写终态回调：只做列表刷新；组件卸载时已由全局注册表 revoke
  const handleSupplementSettled = useCallback(
    (job: JobView) => {
      if (job.status === 'succeeded') onSupplemented?.()
    },
    [onSupplemented],
  )

  async function supplement() {
    if (!id) return
    setSupplementCreateError(null)
    try {
      const accepted = await createSupplement(id)
      setSupplementJobId(accepted.job_id)
      trackedJobIdsRef.current.push(accepted.job_id)
      // 轮询职责上移 App 全局 poller，本组件只保留触发与终态文案派生
      // 后台子任务：不抢占当前产物视图的选择态，避免本组件被卸载而撤销回调
      jobBridge?.track({
        kind: 'supplement',
        jobId: accepted.job_id,
        onSettled: handleSupplementSettled,
        autoSelect: false,
      })
    } catch (e) {
      setSupplementCreateError(describeError(e))
    }
  }

  const refreshSections = useCallback(async () => {
    if (!id) return
    try {
      setSections(await listSections(id))
      setSectionError(null)
    } catch (e) {
      setSectionError(describeError(e))
    }
  }, [id])

  const refreshSummary = useCallback(async () => {
    if (!id) return
    try {
      const item = await getBid(id)
      setOverride(item.summary)
    } catch (e) {
      setSectionError(describeError(e))
    }
    await refreshSections()
    onChanged?.()
  }, [id, onChanged, refreshSections])

  // 重写终态回调：刷新摘要与章节（原私有轮询的终态职责）；卸载时已被 revoke
  const handleRewriteSettled = useCallback(
    (job: JobView) => {
      if (job.status !== 'succeeded') return
      void refreshSummary()
    },
    [refreshSummary],
  )

  async function rewrite(sectionIds: string[] | null) {
    if (!id) return
    if (sectionIds === null && !window.confirm('重写全部未锁定章节？锁定章节会保持不变。')) return
    setRewriteCreateError(null)
    setRewriteSubmitText(sectionIds ? '已提交单章重写任务…' : '已提交批量重写任务…')
    try {
      const accepted = await rewriteSections(id, sectionIds)
      setRewriteJobId(accepted.job_id)
      trackedJobIdsRef.current.push(accepted.job_id)
      // 轮询职责上移 App 全局 poller；终态文案由 rewriteJob 快照派生
      // 后台子任务：不抢占当前产物视图的选择态，避免本组件被卸载而撤销回调
      jobBridge?.track({
        kind: 'rewrite',
        jobId: accepted.job_id,
        onSettled: handleRewriteSettled,
        autoSelect: false,
      })
    } catch (e) {
      setRewriteCreateError(describeError(e))
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

  // T-017：可信度信号统一由纯函数派生，逻辑测试见 tests/bid_quality.test.ts
  const layers = coverageLayers(data)
  const retrievalDegraded = isRetrievalDegraded(data)
  const ruleIssues = ruleErrorSections(data)
  const ruleIssueMap = new Map(ruleIssues.map((issue) => [issue.id, issue]))

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

      {retrievalDegraded && (
        <Banner kind="error">
          检索服务不可用或未建立索引：本次生成没有原文检索依据，引用与证据覆盖为空，
          相关内容请逐条人工核对后再使用。
        </Banner>
      )}

      <Card title="三层覆盖漏斗（计划 → 正文 → 证据）">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {layers.map((layer) => {
            const atRisk = layer.total > 0 && layer.covered < layer.total
            return (
              <div
                key={layer.key}
                className="rounded-lg border border-slate-800 bg-slate-900 p-4"
              >
                <p className="text-xs text-slate-500">{layer.label}</p>
                <p
                  className={`mt-1 text-xl font-semibold ${
                    atRisk ? 'text-amber-400' : 'text-slate-100'
                  }`}
                >
                  {layer.covered}/{layer.total}
                </p>
              </div>
            )
          })}
        </div>
        <p className="mt-3 text-xs text-slate-500">
          计划覆盖：评分因素已分配章节；正文覆盖：非占位章节正文实际提及因素；
          证据覆盖：同时具备同文档检索片段与合法引用。三层构成严格漏斗。
        </p>
        {data.uncovered_factors.length > 0 && (
          <p className="mt-2 text-sm text-amber-400">
            计划层缺口：{data.uncovered_factors.join('、')}
          </p>
        )}
        {!retrievalDegraded &&
          data.evaluation_factors_total > 0 &&
          (data.evidence_factors_covered ?? 0) === 0 && (
            <p className="mt-2 text-sm text-amber-400">
              证据覆盖为 0：正文缺少有效原文引用，请重点核对。
            </p>
          )}
      </Card>

      {ruleIssues.length > 0 && (
        <Banner kind="warn">
          确定性规则检查发现 {ruleIssues.length} 个章节存在问题（引用越界、无检索引用、
          报价禁令或占位符等），详见章节明细表的规则标记。
        </Banner>
      )}

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
              <Banner kind={supplementState === 'failed' || supplementCreateError ? 'error' : 'info'}>
              {supplementMsg}
            </Banner>
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
                <Banner kind={rewriteState === 'failed' || rewriteCreateError ? 'error' : 'info'}>
                {rewriteMsg}
              </Banner>
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
                        <div className="flex flex-wrap items-center gap-1">
                          {locked ? <Badge tone="muted">已锁定</Badge> : <span className="text-slate-500">可重写</span>}
                          {ruleIssueMap.get(s.id) && (
                            <span
                              title={ruleIssueMap
                                .get(s.id)!
                                .errors.map((e) => `${e.code}：${e.message}`)
                                .join('\n')}
                            >
                              <Badge tone="warn">
                                规则 {ruleIssueMap.get(s.id)!.errors.length}
                              </Badge>
                            </span>
                          )}
                        </div>
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

      <PlaceholderWorkbench
        ref={workbenchRef}
        bidId={id}
        docId={data.doc_id}
        bidder={data.bidder}
        sections={sections}
        onSaved={setOverride}
        onRefreshSections={refreshSections}
      />

      <Card title="产物">
        {data.placeholder_count > 0 && (
          <div className="mb-3">
            <Banner kind="warn">
              产物仍含 {data.placeholder_count} 种占位符未填写，下载文件将保留占位标记，请先在工作台填写
            </Banner>
          </div>
        )}
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
            <Button variant="secondary" onClick={() => openPath(bidFileUrl(id, 'bid.docx', true))}>
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
          <div className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-slate-700 bg-slate-950 p-3 text-xs text-slate-300">
            {splitByPlaceholder(editText).map((part, idx) =>
              isPlaceholderMarker(part) ? (
                <mark
                  key={idx}
                  className="cursor-pointer rounded bg-amber-500/30 px-0.5"
                  onClick={() => {
                    const marker = part
                    setEditing(null)
                    workbenchRef.current?.focusMarker(marker)
                  }}
                >
                  {part}
                </mark>
              ) : (
                <span key={idx}>{part}</span>
              ),
            )}
          </div>
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
