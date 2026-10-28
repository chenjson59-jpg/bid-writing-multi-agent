import { useRef, useState, type DragEvent } from 'react'
import { describeError, uploadParse } from '../api'
import { usePolling } from '../hooks'
import { Banner, Button, Card, EmptyState, Field, ProgressBar, Spinner } from '../ui'

const MAX_MB = 200

interface UploadPageProps {
  onRunningChange: (running: boolean) => void
  onUploaded: () => void
  onWrite: (docId: string) => void
}

export function UploadPage({ onRunningChange, onUploaded, onWrite }: UploadPageProps) {
  const [file, setFile] = useState<File | null>(null)
  const [parser, setParser] = useState<'auto' | 'cloud' | 'local'>('auto')
  const [extract, setExtract] = useState(true)
  const [ingest, setIngest] = useState(true)
  const [force, setForce] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const { job } = usePolling(jobId, (doneJob) => {
    onRunningChange(false)
    if (doneJob.status === 'succeeded') onUploaded()
  })

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
    onRunningChange(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const accepted = await uploadParse(
        file,
        { parser, force, extract, ingest },
        setProgress,
        controller.signal,
      )
      setJobId(accepted.job_id)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError(null)
      } else {
        setError(describeError(e))
      }
      onRunningChange(false)
    } finally {
      setUploading(false)
      abortRef.current = null
    }
  }

  function cancelUpload() {
    abortRef.current?.abort()
    setUploading(false)
    onRunningChange(false)
  }

  const done = job && job.status === 'succeeded'

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-100">上传解析</h2>

      {error && <Banner kind="error">{error}</Banner>}

      {!jobId && (
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

      {jobId && (
        <Card title="任务进度">
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
            </div>
          ) : (
            <Spinner />
          )}
        </Card>
      )}

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
          <div className="mt-4">
            {extract ? (
              <Button onClick={() => onWrite(job.result!.doc_id)}>去编写标书</Button>
            ) : (
              <p className="text-sm text-amber-400">未开启结构化抽取，无法编写标书。</p>
            )}
          </div>
        </Card>
      )}

      {!file && !jobId && <EmptyState title="尚未选择文件" hint="支持拖拽或点击选择 PDF 文件" />}
    </div>
  )
}
