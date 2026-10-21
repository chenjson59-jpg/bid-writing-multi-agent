import { useEffect, useMemo, useState } from 'react'
import { createBid, listDocuments } from '../api'
import { usePolling } from '../hooks'
import type { BidCreateBody, BidderProfile, DocumentSummary } from '../types'
import { Banner, Button, Card, EmptyState, Field, Modal } from '../ui'
import { ResultView } from './ResultView'

interface WritePageProps {
  initialDocId: string | null
  onRunningChange: (running: boolean) => void
  onWritten: () => void
}

export function WritePage({ initialDocId, onRunningChange, onWritten }: WritePageProps) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [docId, setDocId] = useState<string>('')
  const [companyName, setCompanyName] = useState('')
  const [legalRep, setLegalRep] = useState('')
  const [registeredCapital, setRegisteredCapital] = useState('')
  const [establishedDate, setEstablishedDate] = useState('')
  const [address, setAddress] = useState('')
  const [qualifications, setQualifications] = useState('')
  const [certifications, setCertifications] = useState('')
  const [advancedJson, setAdvancedJson] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [maxRevisions, setMaxRevisions] = useState('')
  const [minScore, setMinScore] = useState('')
  const [maxSections, setMaxSections] = useState(0)
  const [exportWord, setExportWord] = useState(true)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const { job } = usePolling(jobId, (doneJob) => {
    onRunningChange(false)
    if (doneJob.status === 'succeeded') onWritten()
  })

  useEffect(() => {
    listDocuments()
      .then((docs) => {
        const extractable = docs.filter((d) => d.has_extraction)
        setDocuments(extractable)
        if (initialDocId && extractable.some((d) => d.doc_id === initialDocId)) {
          setDocId(initialDocId)
        } else if (extractable.length > 0) {
          setDocId(extractable[0].doc_id)
        }
      })
      .catch(() => setDocuments([]))
  }, [initialDocId])

  const selectedDoc = useMemo(
    () => documents.find((d) => d.doc_id === docId) ?? null,
    [documents, docId],
  )

  function buildProfile(): BidderProfile {
    if (advancedJson.trim()) {
      return JSON.parse(advancedJson) as BidderProfile
    }
    const profile: BidderProfile = {}
    if (companyName.trim()) profile.company_name = companyName.trim()
    if (legalRep.trim()) profile.legal_representative = legalRep.trim()
    if (registeredCapital.trim()) profile.registered_capital = registeredCapital.trim()
    if (establishedDate.trim()) profile.established_date = establishedDate.trim()
    if (address.trim()) profile.address = address.trim()
    const quals = qualifications.split('\n').map((s) => s.trim()).filter(Boolean)
    if (quals.length) profile.qualifications = quals
    const certs = certifications.split('\n').map((s) => s.trim()).filter(Boolean)
    if (certs.length) profile.certifications = certs
    return profile
  }

  function profileIsEmpty(): boolean {
    if (advancedJson.trim()) {
      try {
        return Object.keys(JSON.parse(advancedJson) as object).length === 0
      } catch {
        return false
      }
    }
    return !companyName && !legalRep && !registeredCapital && !establishedDate && !address && !qualifications.trim() && !certifications.trim()
  }

  function openConfirm() {
    setError(null)
    if (advancedJson.trim()) {
      try {
        JSON.parse(advancedJson)
      } catch {
        setError('高级 JSON 非法，请检查格式')
        return
      }
    }
    setConfirmOpen(true)
  }

  async function submit() {
    setConfirmOpen(false)
    setError(null)
    const body: BidCreateBody = {
      doc_id: docId,
      profile: buildProfile(),
      max_revisions: maxRevisions === '' ? null : Number(maxRevisions),
      min_score: minScore === '' ? null : Number(minScore),
      max_sections: maxSections,
      export_word: exportWord,
    }
    onRunningChange(true)
    try {
      const accepted = await createBid(body)
      setJobId(accepted.job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '提交失败')
      onRunningChange(false)
    }
  }

  const busy = jobId !== null && (!job || job.status === 'pending' || job.status === 'running')

  if (documents.length === 0) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-100">编写标书</h2>
        <EmptyState title="暂无已抽取的文档" hint="请先在「上传解析」页完成结构化抽取" />
      </div>
    )
  }

  if (jobId) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-100">编写标书</h2>
        {busy && (
          <Card title="编写进度">
            <p className="text-sm text-slate-300">
              {job ? (job.status === 'pending' ? '排队中' : '编写中') : '提交中'}
            </p>
            {job?.progress.map((p, i) => (
              <p key={i} className="text-sm text-slate-400">
                {p}
              </p>
            ))}
          </Card>
        )}
        {job?.status === 'failed' && <Banner kind="error">{job.error ?? '编写失败'}</Banner>}
        {job?.status === 'succeeded' && job.bid && <ResultView bid={job.bid} />}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-slate-100">编写标书</h2>

      {error && <Banner kind="error">{error}</Banner>}

      <Card title="选择文档">
        <select
          value={docId}
          onChange={(e) => setDocId(e.target.value)}
          className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
        >
          {documents.map((d) => (
            <option key={d.doc_id} value={d.doc_id}>
              {d.source_name || d.doc_id}
            </option>
          ))}
        </select>
        {selectedDoc && (
          <p className="mt-2 text-xs text-slate-500">
            {selectedDoc.doc_id} · {selectedDoc.pages} 页
          </p>
        )}
      </Card>

      <Card title="投标人资料">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="公司名称">
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="法定代表人">
            <input
              value={legalRep}
              onChange={(e) => setLegalRep(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="注册资本">
            <input
              value={registeredCapital}
              onChange={(e) => setRegisteredCapital(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="成立日期">
            <input
              value={establishedDate}
              onChange={(e) => setEstablishedDate(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="注册地址">
            <input
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="企业资质（每行一项）">
            <textarea
              value={qualifications}
              onChange={(e) => setQualifications(e.target.value)}
              rows={2}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="认证证书（每行一项）">
            <textarea
              value={certifications}
              onChange={(e) => setCertifications(e.target.value)}
              rows={2}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
        </div>

        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="mt-4 text-sm text-sky-400 hover:underline"
        >
          {showAdvanced ? '收起高级 JSON 编辑器' : '展开高级 JSON 编辑器（类似项目、人员、财务等复杂结构）'}
        </button>
        {showAdvanced && (
          <textarea
            value={advancedJson}
            onChange={(e) => setAdvancedJson(e.target.value)}
            rows={8}
            placeholder='{"similar_projects": [...], "personnel": [...], "financials": {...}}'
            className="mt-2 w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 font-mono text-xs text-slate-100"
          />
        )}
      </Card>

      <Card title="编写参数">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Field label="最大修订次数（留空用默认）">
            <input
              type="number"
              min={0}
              max={3}
              value={maxRevisions}
              onChange={(e) => setMaxRevisions(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="评审阈值（留空用默认）">
            <input
              type="number"
              min={0}
              max={100}
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="章节数量（0 表示全部）">
            <input
              type="number"
              min={0}
              max={50}
              value={maxSections}
              onChange={(e) => setMaxSections(Number(e.target.value))}
              className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            />
          </Field>
          <Field label="导出 Word">
            <div className="flex h-9 items-center">
              <input type="checkbox" checked={exportWord} onChange={(e) => setExportWord(e.target.checked)} />
            </div>
          </Field>
        </div>
        <div className="mt-4">
          <Button onClick={openConfirm}>提交编写</Button>
        </div>
      </Card>

      <Modal open={confirmOpen} title="确认编写" onClose={() => setConfirmOpen(false)}>
        <div className="space-y-2 text-sm text-slate-300">
          <p>文档：{selectedDoc?.source_name || docId}</p>
          <p>投标人：{companyName || '（未填写）'}</p>
          <p>章节范围：{maxSections === 0 ? '全部章节' : `最多 ${maxSections} 章`}</p>
          <p>修订：{maxRevisions === '' ? '默认' : `最多 ${maxRevisions} 次`}</p>
          <p>Word：{exportWord ? '导出' : '不导出'}</p>
        </div>
        {maxSections === 0 && (
          <p className="mt-3 text-sm text-amber-400">编写全部章节可能耗时较长并产生模型调用费用，请确认。</p>
        )}
        {profileIsEmpty() && (
          <p className="mt-3 text-sm text-amber-400">投标人资料为空，将生成大量占位符，请确认。</p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmOpen(false)}>
            取消
          </Button>
          <Button onClick={submit}>确认</Button>
        </div>
      </Modal>
    </div>
  )
}
