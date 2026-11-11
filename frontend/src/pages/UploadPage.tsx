import { useEffect, useRef, useState, type DragEvent } from 'react'
import { describeError, uploadParse } from '../api'
import { derivePageJobState, shouldEmitJobSuccess } from '../lib/task_state'
import type { JobView } from '../types'
import { Banner, Button, Card, EmptyState, Field, ProgressBar, Spinner } from '../ui'

const MAX_MB = 200
// 「查看」聚焦后任务卡片高亮时长
const FOCUS_HIGHLIGHT_MS = 2000

interface UploadPageProps {
  /** 页面提交 parse 任务时调这个，把 jobId 上交给 App 全局 poll */
  onRegisterJob: (jobId: string) => void
  /** T-034：显式关闭当前通道选中任务（取消/失败/再上传），回表单 */
  onCloseJob: () => void
  /** T-034：任务进行中返回表单，任务继续后台轮询（不关闭、不停止） */
  onDeselect: () => void
  /** App 全局 poll 回来的最新任务状态（切 tab 也不会丢） */
  activeJob: JobView | null
  /** T-031：App 持有的受控选择态，决定页面展示表单还是任务视图 */
  selectedJobId: string | null
  /** 「查看」聚焦信号，非 0 时滚动并高亮任务卡片 */
  focusNonce: number
  onUploaded: () => void
  onWrite: (docId: string) => void
}

export function UploadPage({
  onRegisterJob,
  onCloseJob,
  onDeselect,
  activeJob,
  selectedJobId,
  focusNonce,
  onUploaded,
  onWrite,
}: UploadPageProps) {
  const [file, setFile] = useState<File | null>(null)
  const [parser, setParser] = useState<'auto' | 'cloud' | 'local'>('auto')
  const [extract, setExtract] = useState(true)
  const [ingest, setIngest] = useState(true)
  const [force, setForce] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [highlighted, setHighlighted] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const taskCardRef = useRef<HTMLDivElement | null>(null)
  const highlightTimerRef = useRef<number | null>(null)

  // T-031：任务视图完全由 App 受控选择态 + activeJob 派生，页面不再自持 jobId
  const view = derivePageJobState(selectedJobId, activeJob)
  const job = view.mode === 'job' ? view.job : null
  const done = !!job && job.status === 'succeeded'

  // 任务成功时刷新文档列表计数（任务完成后页面可能已经切走）。
  // T-030：按 jobId 记账，复位后新任务成功可再次触发；旧布尔闩锁会吞掉第二次成功回调。
  const firedJobRef = useRef<string | null>(null)
  useEffect(() => {
    const jobId = activeJob?.job_id ?? null
    if (shouldEmitJobSuccess(activeJob, selectedJobId, firedJobRef.current) && jobId) {
      firedJobRef.current = jobId
      onUploaded()
    }
  }, [activeJob, selectedJobId, onUploaded])

  // 「查看」反馈：滚动到任务卡片并短暂高亮（同 tab 点击也有明确反馈）
  useEffect(() => {
    if (focusNonce === 0 || view.mode === 'form') return
    taskCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setHighlighted(true)
    if (highlightTimerRef.current !== null) window.clearTimeout(highlightTimerRef.current)
    highlightTimerRef.current = window.setTimeout(() => setHighlighted(false), FOCUS_HIGHLIGHT_MS)
    return () => {
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current)
        highlightTimerRef.current = null
      }
    }
  }, [focusNonce, view.mode])

  function pickFile(f: File | null) {
    setError(null)
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.pdf')) {
      setError('仅支持 PDF 文件')
      return
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      setError(`文件超过大小上限 ${MAX_MB} MB`)
      return
    }
    setFile(f)
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault()
    pickFile(e.dataTransfer.files[0] ?? null)
  }

  async function submit() {
    if (!file) return
    setError(null)
    setUploading(true)
    setProgress(0)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const accepted = await uploadParse(
        file,
        { parser, force, extract, ingest },
        setProgress,
        controller.signal,
      )
      // 选择态交给 App：registerJob 内部置位 selectedJobIds，首帧前显示加载态
      onRegisterJob(accepted.job_id)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError(null)
      } else {
        setError(describeError(e))
      }
      onCloseJob()
    } finally {
      setUploading(false)
      abortRef.current = null
    }
  }

  function cancelUpload() {
    abortRef.current?.abort()
    setUploading(false)
    onCloseJob()
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-100">上传解析</h2>

      {error && <Banner kind="error">{error}</Banner>}

      {view.mode === 'form' && (
        <Card>
          <div
            className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-700 py-12"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
          >
            <p className="text-sm text-slate-400">拖拽 PDF 到此处，或</p>
            <label className="mt-3 cursor-pointer">
              <span className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500">
                选择文件
              </span>
              <input
                type="file"
                accept=".pdf,application/pdf"
                className="hidden"
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
              />
            </label>
            {file && <p className="mt-3 text-sm text-slate-300">{file.name}</p>}
          </div>

          <div className="mt-5 space-y-4">
            <Field label="解析方式">
              <select
                value={parser}
                onChange={(e) => setParser(e.target.value as 'auto' | 'cloud' | 'local')}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              >
                <option value="auto">自动</option>
                <option value="cloud">云端</option>
                <option value="local">本地</option>
              </select>
            </Field>

            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={extract} onChange={(e) => setExtract(e.target.checked)} />
                结构化抽取（编写标书依赖）
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={ingest} onChange={(e) => setIngest(e.target.checked)} />
                向量入库（检索依赖）
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                强制重新解析
              </label>
            </div>

            {uploading ? (
              <div className="space-y-2">
                <ProgressBar percent={progress} />
                <div className="flex items-center gap-3">
                  <span className="text-sm text-slate-400">上传中 {progress}%</span>
                  <Button variant="danger" onClick={cancelUpload}>
                    取消
                  </Button>
                </div>
              </div>
            ) : (
              <Button onClick={submit} disabled={!file}>
                开始解析
              </Button>
            )}
          </div>
        </Card>
      )}

      {view.mode !== 'form' && (
        <div
          ref={taskCardRef}
          className={highlighted ? 'rounded-lg ring-2 ring-sky-400' : undefined}
          data-testid="parse-task-card"
        >
          <Card title="任务进度">
            {(view.mode === 'submitting' ||
              job?.status === 'pending' ||
              job?.status === 'running') && (
              <div className="mb-3 flex justify-end">
                <Button variant="ghost" onClick={onDeselect}>
                  返回表单（后台继续）
                </Button>
              </div>
            )}
            {job ? (
              <div className="space-y-3">
                <p className="text-sm text-slate-300">
                  状态：
                  {job.status === 'pending' && '排队中'}
                  {job.status === 'running' && '运行中'}
                  {job.status === 'succeeded' && '已完成'}
                  {job.status === 'failed' && '失败'}
                </p>
                {job.progress.length > 0 && (
                  <ul className="space-y-1 text-sm text-slate-400">
                    {job.progress.map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                )}
                {job.error && <Banner kind="error">{job.error}</Banner>}
                {job.status === 'failed' && (
                  <div className="mt-3">
                    <Button variant="secondary" onClick={() => onCloseJob()}>
                      重新上传
                    </Button>
                  </div>
                )}
              </div>
            ) : (
              <Spinner />
            )}
          </Card>

          {done && job?.result && (
            <Card title="解析结果">
              <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                <p className="text-slate-400">文档类型<span className="block text-slate-200">{job.result.doc_type}</span></p>
                <p className="text-slate-400">解析器<span className="block text-slate-200">{job.result.parser}</span></p>
                <p className="text-slate-400">页数<span className="block text-slate-200">{job.result.pages}</span></p>
                <p className="text-slate-400">章节数<span className="block text-slate-200">{job.result.sections}</span></p>
              </div>
              {job.extraction && (
                <p className="mt-3 text-sm text-slate-400">
                  抽取摘要：评分因素 {job.extraction.evaluation_factors ?? '-'} · 格式条目 {job.extraction.bid_format_items ?? '-'}
                </p>
              )}
              {job.ingest && (
                <p className="mt-1 text-sm text-slate-400">
                  入库摘要：新增 {job.ingest.added ?? '-'} 片段
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                {extract ? (
                  <Button onClick={() => onWrite(job.result!.doc_id)}>去编写标书</Button>
                ) : (
                  <p className="text-sm text-amber-400">未开启结构化抽取，无法编写标书。</p>
                )}
                <Button variant="secondary" onClick={() => onCloseJob()}>
                  再上传一个
                </Button>
              </div>
            </Card>
          )}
        </div>
      )}

      {!file && view.mode === 'form' && <EmptyState title="尚未选择文件" hint="支持拖拽或点击选择 PDF 文件" />}
    </div>
  )
}
