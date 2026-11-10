import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  createBid,
  createProfile,
  deleteProfile,
  describeError,
  getProfileSchema,
  listDocuments,
  listProfiles,
  updateProfile,
} from '../api'
import type {
  BidCreateBody,
  BidderProfile,
  BidderProfileItem,
  DocumentSummary,
  JobView,
  ProfileSchemaItem,
  ProfileSchemaResponse,
  SchemaLevel,
} from '../types'
import { derivePageJobState, type JobBridge } from '../lib/task_state'
import { Banner, Button, Card, EmptyState, Field, Modal } from '../ui'
import { ResultView } from './ResultView'

interface WritePageProps {
  initialDocId: string | null
  /** 页面触发编写任务时调这个，把 jobId 上交给 App 全局 poll */
  onRegisterJob: (jobId: string) => void
  /** T-034：显式关闭当前通道选中任务（返回修改参数/再写一份），回表单 */
  onCloseJob: () => void
  /** T-034：任务进行中返回表单，任务继续后台轮询（不关闭、不停止） */
  onDeselect: () => void
  /** App 全局 poll 回来的最新任务状态（切 tab 也不会丢） */
  activeJob: JobView | null
  /** T-031：App 持有的受控选择态，决定页面展示表单还是任务视图 */
  selectedJobId: string | null
  /** 「查看」聚焦信号，非 0 时滚动并高亮任务卡片 */
  focusNonce: number
  /** T-034：ResultView 登记补写/重写任务用的全局桥接 */
  jobBridge?: JobBridge
  onWritten: () => void
}

// 复杂资料拆成结构化表单，业务人员按字段填写，不必手写 JSON
interface ProjectRow {
  project_name: string
  project_type: string
  scale: string
  contract_amount: string
  role: string
  start_date: string
  completion_date: string
  location: string
  owner: string
}

interface PersonRow {
  position: string
  name: string
  certificate: string
  safety_cert: string
  experience_years: string
}

interface FinancialsForm {
  report_period: string
  total_assets: string
  total_liabilities: string
  owner_equity: string
  operating_revenue: string
  net_profit: string
  operating_cash_flow: string
  current_ratio: string
  quick_ratio: string
  debt_ratio: string
  return_on_equity: string
  audit_opinion: string
}

interface ContactForm {
  address: string
  postal_code: string
  phone: string
  fax: string
  email: string
  website: string
  bank: string
  bank_account: string
}

const emptyProject: ProjectRow = {
  project_name: '',
  project_type: '',
  scale: '',
  contract_amount: '',
  role: '',
  start_date: '',
  completion_date: '',
  location: '',
  owner: '',
}

const emptyPerson: PersonRow = {
  position: '',
  name: '',
  certificate: '',
  safety_cert: '',
  experience_years: '',
}

const emptyFinancials: FinancialsForm = {
  report_period: '',
  total_assets: '',
  total_liabilities: '',
  owner_equity: '',
  operating_revenue: '',
  net_profit: '',
  operating_cash_flow: '',
  current_ratio: '',
  quick_ratio: '',
  debt_ratio: '',
  return_on_equity: '',
  audit_opinion: '',
}

const emptyContact: ContactForm = {
  address: '',
  postal_code: '',
  phone: '',
  fax: '',
  email: '',
  website: '',
  bank: '',
  bank_account: '',
}

// 结构化表单里的输入框统一样式
const INPUT_CLASS = 'w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100'

// 更新列表里某一行，只改变化的字段
function updateRow<T>(rows: T[], index: number, patch: Partial<T>): T[] {
  return rows.map((row, i) => (i === index ? { ...row, ...patch } : row))
}

// 回填：把资料库里的任意结构按模板字段取字符串
function toRows<T extends object>(src: unknown, template: T): T[] {
  if (!Array.isArray(src)) return []
  return src
    .filter((item) => item && typeof item === 'object')
    .map((item) => {
      const row: Record<string, string> = {}
      for (const key of Object.keys(template)) {
        row[key] = String((item as Record<string, unknown>)[key] ?? '')
      }
      return row as unknown as T
    })
}

function toForm<T extends object>(src: unknown, template: T): T {
  const row: Record<string, string> = {}
  for (const key of Object.keys(template)) {
    row[key] =
      src && typeof src === 'object'
        ? String((src as Record<string, unknown>)[key] ?? '')
        : ''
  }
  return row as unknown as T
}

// 提交前清理：去掉空白字段和整行全空的记录
function compactRows<T extends object>(rows: T[]): Record<string, string>[] {
  return rows
    .map((row) => {
      const out: Record<string, string> = {}
      for (const [key, value] of Object.entries(row)) {
        const trimmed = String(value).trim()
        if (trimmed) out[key] = trimmed
      }
      return out
    })
    .filter((row) => Object.keys(row).length > 0)
}

function compactForm<T extends object>(form: T): Record<string, string> | null {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(form)) {
    const trimmed = String(value).trim()
    if (trimmed) out[key] = trimmed
  }
  return Object.keys(out).length > 0 ? out : null
}

export function WritePage({
  initialDocId,
  onRegisterJob,
  onCloseJob,
  onDeselect,
  activeJob,
  selectedJobId,
  focusNonce,
  jobBridge,
  onWritten,
}: WritePageProps) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [docId, setDocId] = useState<string>('')
  const [companyName, setCompanyName] = useState('')
  const [legalRep, setLegalRep] = useState('')
  const [registeredCapital, setRegisteredCapital] = useState('')
  const [establishedDate, setEstablishedDate] = useState('')
  const [address, setAddress] = useState('')
  const [qualifications, setQualifications] = useState('')
  const [certifications, setCertifications] = useState('')
  const [projects, setProjects] = useState<ProjectRow[]>([])
  const [persons, setPersons] = useState<PersonRow[]>([])
  const [financials, setFinancials] = useState<FinancialsForm>(emptyFinancials)
  const [contact, setContact] = useState<ContactForm>(emptyContact)
  const [maxRevisions, setMaxRevisions] = useState('')
  const [minScore, setMinScore] = useState('')
  const [maxSections, setMaxSections] = useState(0)
  const [exportWord, setExportWord] = useState(true)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [profiles, setProfiles] = useState<BidderProfileItem[]>([])
  const [profileId, setProfileId] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [lastBody, setLastBody] = useState<BidCreateBody | null>(null)
  const [precheckOpen, setPrecheckOpen] = useState(false)
  const [missingFields, setMissingFields] = useState<
    Array<{ key: keyof BidderProfile; label: string }>
  >([])
  // 后端根据抽取结果推断的资料字段级别：required / recommended / optional
  const [schema, setSchema] = useState<ProfileSchemaResponse | null>(null)
  const [schemaLoading, setSchemaLoading] = useState(false)
  const [schemaError, setSchemaError] = useState<string | null>(null)
  // optional 字段默认折叠，用户可展开查看
  const [optionalOpen, setOptionalOpen] = useState(false)

  const refreshProfiles = useCallback(async () => {
    try {
      setProfiles(await listProfiles())
    } catch {
      setProfiles([])
    }
  }, [])

  // activeJob 是 App 全局 poll 回来的最新状态。当前选中任务终态时刷新侧栏计数与资料库；
  // T-030 起终态快照留存、显式关闭才清理，不再有 grace 定时器。
  useEffect(() => {
    if (!activeJob) return
    if (activeJob.status === 'succeeded') {
      onWritten()
      refreshProfiles()
    }
  }, [activeJob?.status, activeJob?.job_id, onWritten, refreshProfiles])

  useEffect(() => {
    refreshProfiles()
  }, [refreshProfiles])

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

  // 选中文档变化时，向服务端询问这份标书需要投标人填哪些字段
  useEffect(() => {
    if (!selectedDoc) {
      setSchema(null)
      setSchemaError(null)
      return
    }
    let cancelled = false
    setSchemaLoading(true)
    setSchemaError(null)
    getProfileSchema(selectedDoc.doc_id)
      .then((resp) => {
        if (!cancelled) {
          setSchema(resp)
          setOptionalOpen(false)
          setSchemaError(null)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          // schema 端点调用失败时降级为不影响现有表单，
          // 但给业务人员一个小提示，让他知道哪些字段"按通用规则"可能需要填
          setSchema(null)
          setSchemaError(
            e && typeof e === 'object' && 'message' in e
              ? String((e as Error).message)
              : '无法根据标书内容自动判断哪些字段必填，以下按通用规则显示',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setSchemaLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedDoc?.doc_id])

  // 取字段级别；schema 缺失时按 required 兜底（不影响业务）
  function levelOf(key: string): SchemaLevel {
    const item = schema?.fields[key]
    if (item?.level) return item.level
    // core 字段任何时候都必填
    if (['company_name', 'legal_representative', 'registered_capital', 'established_date', 'address'].includes(key)) {
      return 'required'
    }
    // schema 还没加载到（null / 请求失败）→ 安全降级为 optional，
    // 避免把标书没提过的字段都算"缺失"。schema 加载成功后会自动变成正确级别。
    return 'optional'
  }

  function schemaItemOf(key: string): ProfileSchemaItem | undefined {
    return schema?.fields[key]
  }

  // 给 Field 组件用：按级别返回 label 后缀和 error/warn 状态
  function labelSuffix(level: SchemaLevel): string {
    if (level === 'required') return ' *'
    if (level === 'recommended') return ' 建议'
    return ''
  }
  function labelClass(level: SchemaLevel): string {
    if (level === 'required') return 'text-slate-100 after:text-rose-500 after:content-["*"]'
    if (level === 'recommended') return 'text-amber-400'
    return 'text-slate-500'
  }

  function buildProfile(): BidderProfile {
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
    // 结构化表单收集，空行/空字段不提交
    const projectRows = compactRows(projects)
    if (projectRows.length) profile.similar_projects = projectRows as unknown as Record<string, unknown>[]
    const personRows = compactRows(persons)
    if (personRows.length) profile.personnel = personRows as unknown as Record<string, unknown>[]
    const financialForm = compactForm(financials)
    if (financialForm) profile.financials = financialForm
    const contactForm = compactForm(contact)
    if (contactForm) profile.contact = contactForm
    return profile
  }

  function profileIsEmpty(): boolean {
    const basicEmpty =
      !companyName &&
      !legalRep &&
      !registeredCapital &&
      !establishedDate &&
      !address &&
      !qualifications.trim() &&
      !certifications.trim()
    return basicEmpty && compactRows(projects).length === 0 && compactRows(persons).length === 0
  }

  // 与后端 PROFILE_FIELD_LABELS / MISSING_FIELD_ORDER 保持一致
  const PROFILE_FIELD_ORDER: Array<[keyof BidderProfile, string]> = [
    ['company_name', '公司名称'],
    ['legal_representative', '法定代表人'],
    ['registered_capital', '注册资本'],
    ['established_date', '成立日期'],
    ['address', '注册地址'],
    ['qualifications', '企业资质'],
    ['certifications', '认证证书'],
    ['similar_projects', '类似项目业绩'],
    ['personnel', '项目管理人员'],
    ['financials', '财务数据'],
    ['contact', '联系方式'],
  ]

  // 复杂资料对应的结构化区块，填写位置与基础输入框不同
  const ADVANCED_FIELD_KEYS = new Set<string>([
    'similar_projects',
    'personnel',
    'financials',
    'contact',
  ])

  function missingProfileEntries(): Array<{ key: keyof BidderProfile; label: string }> {
    const p = buildProfile() as Record<string, unknown>
    return PROFILE_FIELD_ORDER.filter(([key]) => {
      // 标书没提过的 optional 字段不算缺失
      if (levelOf(key as string) === 'optional') return false
      const v = p[key]
      return (
        v === undefined ||
        v === null ||
        v === '' ||
        (Array.isArray(v) && v.length === 0) ||
        (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0)
      )
    }).map(([key, label]) => ({ key, label }))
  }

  // 把用户带到真正该填写的位置
  function goToField(key: keyof BidderProfile) {
    setPrecheckOpen(false)
    // 列表类资料还没有行时，先插入一条空行，否则跳过去也无可输入
    if (key === 'similar_projects' && projects.length === 0) {
      setProjects([{ ...emptyProject }])
    }
    if (key === 'personnel' && persons.length === 0) {
      setPersons([{ ...emptyPerson }])
    }
    window.setTimeout(() => {
      const el = document.getElementById(`profile-${String(key)}`)
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      const input = el?.querySelector('input, textarea')
      if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) {
        input.focus({ preventScroll: true })
      }
    }, 150)
  }

  function openConfirm() {
    setError(null)
    // 编写前预检：缺资料时先提示，避免生成一堆【待填写】占位符
    const missing = missingProfileEntries()
    if (missing.length > 0) {
      setMissingFields(missing)
      setPrecheckOpen(true)
      return
    }
    setConfirmOpen(true)
  }

  // 资料库 → 表单：基础字段进输入框，业绩/人员/财务/联系方式回填到结构化区块
  function applyProfile(item: BidderProfileItem) {
    const d = item.data ?? {}
    setCompanyName(d.company_name || item.company_name)
    setLegalRep(d.legal_representative || '')
    setRegisteredCapital(d.registered_capital || '')
    setEstablishedDate(d.established_date || '')
    setAddress(d.address || '')
    setQualifications((d.qualifications ?? []).join('\n'))
    setCertifications((d.certifications ?? []).join('\n'))
    setProjects(toRows(d.similar_projects, emptyProject))
    setPersons(toRows(d.personnel, emptyPerson))
    setFinancials(toForm(d.financials, emptyFinancials))
    setContact(toForm(d.contact, emptyContact))
    setProfileId(item.id)
    setError(null)
    setNotice(`已载入「${item.company_name}」的资料`)
  }

  async function saveToLibrary() {
    setError(null)
    setNotice(null)
    if (!companyName.trim()) {
      setError('请先填写公司名称，再保存资料')
      return
    }
    const body = { company_name: companyName.trim(), data: buildProfile() }
    const isUpdate = profileId !== null
    try {
      const saved = isUpdate
        ? await updateProfile(profileId as number, body)
        : await createProfile(body)
      setProfileId(saved.id)
      await refreshProfiles()
      setNotice(isUpdate ? '已覆盖保存到资料库' : '已保存到资料库')
    } catch (e) {
      setError(describeError(e))
    }
  }

  async function removeFromLibrary() {
    if (profileId === null) return
    if (!window.confirm('确认删除这份资料？已生成的标书不受影响。')) return
    setError(null)
    setNotice(null)
    try {
      await deleteProfile(profileId)
      setProfileId(null)
      await refreshProfiles()
      setNotice('已从资料库删除')
    } catch (e) {
      setError(describeError(e))
    }
  }

  async function startBid(body: BidCreateBody) {
    setConfirmOpen(false)
    setError(null)
    setLastBody(body)
    try {
      const accepted = await createBid(body)
      // 选择态由 App 的 registerJob 统一置位，页面不再自持 jobId（T-031）
      onRegisterJob(accepted.job_id)
    } catch (e) {
      setError(describeError(e))
    }
  }

  function submit() {
    startBid({
      doc_id: docId,
      profile: buildProfile(),
      max_revisions: maxRevisions === '' ? null : Number(maxRevisions),
      min_score: minScore === '' ? null : Number(minScore),
      max_sections: maxSections,
      export_word: exportWord,
    })
  }

  // T-031：任务视图由 App 受控选择态 + activeJob 三态派生，切 tab 重挂不丢
  const view = derivePageJobState(selectedJobId, activeJob)
  const localJob = view.mode === 'job' ? view.job : null
  const busy =
    view.mode === 'submitting' ||
    localJob?.status === 'pending' ||
    localJob?.status === 'running'

  // 「查看」反馈：滚动到任务卡片并短暂高亮（已在目标 tab 时点击也有明确反馈）
  const [highlighted, setHighlighted] = useState(false)
  const taskCardRef = useRef<HTMLDivElement | null>(null)
  const highlightTimerRef = useRef<number | null>(null)
  useEffect(() => {
    if (focusNonce === 0 || view.mode === 'form') return
    taskCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setHighlighted(true)
    if (highlightTimerRef.current !== null) window.clearTimeout(highlightTimerRef.current)
    highlightTimerRef.current = window.setTimeout(() => setHighlighted(false), 2000)
    return () => {
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current)
        highlightTimerRef.current = null
      }
    }
  }, [focusNonce, view.mode])
  // 资料完整度实时提示，编写前就能看到还缺什么、该去哪填
  const liveMissing = missingProfileEntries()
  const liveMissingSet = new Set(liveMissing.map((m) => String(m.key)))
  const liveAdvancedMissing = liveMissing.filter((m) => ADVANCED_FIELD_KEYS.has(String(m.key)))

  // T-031：任务视图优先于「暂无文档」EmptyState——重挂瞬间 documents 异步未返回时，
  // 受控选择态仍应直接恢复任务视图，不被空态覆盖
  if (view.mode !== 'form') {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-100">编写标书</h2>
        <div
          ref={taskCardRef}
          className={highlighted ? 'rounded-lg ring-2 ring-sky-400' : undefined}
          data-testid="bid-task-card"
        >
          {busy && (
            <Card title="编写进度">
              <p className="text-sm text-slate-300">
                {localJob ? (localJob.status === 'pending' ? '排队中' : '编写中') : '提交中'}
              </p>
              {localJob?.progress.map((p, i) => (
                <p key={i} className="text-sm text-slate-400">
                  {p}
                </p>
              ))}
              <div className="mt-3 flex justify-end">
                <Button variant="ghost" onClick={onDeselect}>
                  返回表单（后台继续）
                </Button>
              </div>
            </Card>
          )}
          {localJob?.status === 'failed' && (
            <Card title="编写失败">
              <Banner kind="error">{localJob.error ?? '编写失败'}</Banner>
              <div className="mt-3 flex gap-2">
                {lastBody && (
                  <Button onClick={() => startBid(lastBody)}>重试（沿用原参数）</Button>
                )}
                <Button variant="secondary" onClick={() => onCloseJob()}>
                  返回修改参数
                </Button>
              </div>
            </Card>
          )}
          {localJob?.status === 'succeeded' && localJob.bid && (
            <div className="space-y-4">
              {/* T-034：补写终态经全局 onSettled 回调刷新侧栏计数（HistoryPage 走 nonce 单路径防双刷） */}
              <ResultView bid={localJob.bid} jobBridge={jobBridge} onSupplemented={onWritten} />
              {/* T-030：终态结果持续留存，显式复位才回表单（grace 自动清理已移除） */}
              <Button variant="secondary" onClick={() => onCloseJob()}>
                再写一份
              </Button>
            </div>
          )}
        </div>
      </div>
    )
  }

  if (documents.length === 0) {
    return (
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-100">编写标书</h2>
        <EmptyState title="暂无已抽取的文档" hint="请先在「上传解析」页完成结构化抽取" />
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
        {selectedDoc && !selectedDoc.extraction_reviewed && (
          <div className="mt-2">
            <Banner kind="warn">
              该文档抽取结果尚未人工核对，建议先在「文档库 → 抽取结果」核对评分因素与格式条目，再开始编写。
            </Banner>
          </div>
        )}
      </Card>

      <Card title="投标人资料">
        <div className="mb-4 flex flex-wrap items-end gap-3 rounded-md border border-slate-800 bg-slate-800/40 p-3">
          <Field label="从资料库载入">
            <select
              value={profileId ?? ''}
              onChange={(e) => {
                const item = profiles.find((p) => p.id === Number(e.target.value))
                if (item) applyProfile(item)
                else setProfileId(null)
              }}
              className="w-60 rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
            >
              <option value="">（未选择）</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.company_name}
                </option>
              ))}
            </select>
          </Field>
          <Button variant="secondary" onClick={saveToLibrary}>
            保存到资料库
          </Button>
          {profileId !== null && (
            <Button variant="danger" onClick={removeFromLibrary}>
              删除该资料
            </Button>
          )}
          <span className="pb-2 text-xs text-slate-500">
            {profiles.length === 0
              ? '暂无历史资料（需数据库可用）'
              : `共 ${profiles.length} 份，同公司名覆盖保存`}
          </span>
        </div>
        {notice && <Banner kind="info">{notice}</Banner>}
        {liveMissing.length > 0 && (
          <div className="mb-3">
            <Banner kind="warn">
              尚缺 {liveMissing.length} 项资料：{liveMissing.map((m) => m.label).join('、')}。
              {liveAdvancedMissing.length > 0 &&
                `其中「${liveAdvancedMissing.map((m) => m.label).join('、')}」需在下方对应的资料区块中逐项填写。`}
              缺失项会在标书中留下【待填写】占位符。
            </Banner>
          </div>
        )}
        {schemaLoading && (
          <p className="mb-2 text-xs text-slate-500">正在根据标书抽取结果判断需要填写的资料字段...</p>
        )}
        {schemaError && !schemaLoading && (
          <Banner kind="warn">
            自动判断字段级别失败：{schemaError}。以下按通用规则显示（核心字段必填，其余折叠为可选）。
          </Banner>
        )}
        {!schemaLoading && schema && (
          <p className="mb-2 text-xs text-slate-500">
            已根据该标书的评分因素、资质要求和格式条目自动标注：
            <span className="text-rose-400">红色 * 为必填</span>，
            <span className="text-amber-400">黄色为建议填写</span>，
            灰色可选字段默认折叠。
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {levelOf('company_name') !== 'optional' && (
            <Field
              label={`公司名称${labelSuffix(levelOf('company_name'))}`}
              labelClass={labelClass(levelOf('company_name'))}
              error={liveMissingSet.has('company_name') ? '未填写' : undefined}
            >
              <input
                id="profile-company_name"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              />
            </Field>
          )}
          {levelOf('legal_representative') !== 'optional' && (
            <Field
              label={`法定代表人${labelSuffix(levelOf('legal_representative'))}`}
              labelClass={labelClass(levelOf('legal_representative'))}
              error={liveMissingSet.has('legal_representative') ? '未填写' : undefined}
            >
              <input
                id="profile-legal_representative"
                value={legalRep}
                onChange={(e) => setLegalRep(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              />
            </Field>
          )}
          {levelOf('registered_capital') !== 'optional' && (
            <Field
              label={`注册资本${labelSuffix(levelOf('registered_capital'))}`}
              labelClass={labelClass(levelOf('registered_capital'))}
              error={liveMissingSet.has('registered_capital') ? '未填写' : undefined}
            >
              <input
                id="profile-registered_capital"
                value={registeredCapital}
                onChange={(e) => setRegisteredCapital(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              />
            </Field>
          )}
          {levelOf('established_date') !== 'optional' && (
            <Field
              label={`成立日期${labelSuffix(levelOf('established_date'))}`}
              labelClass={labelClass(levelOf('established_date'))}
              error={liveMissingSet.has('established_date') ? '未填写' : undefined}
            >
              <input
                id="profile-established_date"
                value={establishedDate}
                onChange={(e) => setEstablishedDate(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              />
            </Field>
          )}
          {levelOf('address') !== 'optional' && (
            <Field
              label={`注册地址${labelSuffix(levelOf('address'))}`}
              labelClass={labelClass(levelOf('address'))}
              error={liveMissingSet.has('address') ? '未填写' : undefined}
            >
              <input
                id="profile-address"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              />
            </Field>
          )}
          {levelOf('qualifications') !== 'optional' && (
            <Field
              label={`企业资质（每行一项）${labelSuffix(levelOf('qualifications'))}`}
              labelClass={labelClass(levelOf('qualifications'))}
              error={liveMissingSet.has('qualifications') ? '未填写' : undefined}
            >
              <textarea
                id="profile-qualifications"
                value={qualifications}
                onChange={(e) => setQualifications(e.target.value)}
                rows={2}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              />
            </Field>
          )}
          {levelOf('certifications') !== 'optional' && (
            <Field
              label={`认证证书（每行一项）${labelSuffix(levelOf('certifications'))}`}
              labelClass={labelClass(levelOf('certifications'))}
              error={liveMissingSet.has('certifications') ? '未填写' : undefined}
            >
              <textarea
                id="profile-certifications"
                value={certifications}
                onChange={(e) => setCertifications(e.target.value)}
                rows={2}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              />
            </Field>
          )}
        </div>

        {/* 结构化区块：按 schema 级别决定是否显示 */}
        <div className="mt-5 space-y-6 border-t border-slate-800 pt-4">
          {/* 类似项目业绩 */}
          {levelOf('similar_projects') !== 'optional' && (
          <div id="profile-similar_projects">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-medium text-slate-200">
                类似项目业绩
                {levelOf('similar_projects') === 'required' && (
                  <span className="ml-1 text-rose-500">*</span>
                )}
                {levelOf('similar_projects') === 'recommended' && (
                  <span className="ml-1 text-amber-400">建议</span>
                )}
                <span className="ml-2 text-xs text-slate-500">已填 {projects.length} 条</span>
                {liveMissingSet.has('similar_projects') && (
                  <span className="ml-2 text-xs text-amber-400">未填写</span>
                )}
              </h3>
              <Button
                variant="secondary"
                onClick={() => setProjects((rows) => [...rows, { ...emptyProject }])}
              >
                + 添加业绩
              </Button>
            </div>
            {projects.length === 0 && (
              <p className="text-xs text-slate-500">点「添加业绩」至少录入 1 个类似项目</p>
            )}
            <div className="space-y-3">
              {projects.map((row, index) => (
                <div key={index} className="rounded-md border border-slate-700 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs text-slate-400">业绩 {index + 1}</span>
                    <button
                      type="button"
                      onClick={() => setProjects((rows) => rows.filter((_, i) => i !== index))}
                      className="text-xs text-rose-400 hover:underline"
                    >
                      删除
                    </button>
                  </div>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                    <Field label="项目名称">
                      <input
                        className={INPUT_CLASS}
                        value={row.project_name}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { project_name: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="项目类型">
                      <input
                        className={INPUT_CLASS}
                        value={row.project_type}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { project_type: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="项目规模">
                      <input
                        className={INPUT_CLASS}
                        value={row.scale}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { scale: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="合同金额">
                      <input
                        className={INPUT_CLASS}
                        value={row.contract_amount}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { contract_amount: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="承担角色">
                      <input
                        className={INPUT_CLASS}
                        value={row.role}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { role: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="业主单位">
                      <input
                        className={INPUT_CLASS}
                        value={row.owner}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { owner: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="开始时间">
                      <input
                        className={INPUT_CLASS}
                        placeholder="如 2022-03"
                        value={row.start_date}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { start_date: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="完成时间">
                      <input
                        className={INPUT_CLASS}
                        placeholder="如 2024-01"
                        value={row.completion_date}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { completion_date: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="项目地点">
                      <input
                        className={INPUT_CLASS}
                        value={row.location}
                        onChange={(e) =>
                          setProjects((rows) => updateRow(rows, index, { location: e.target.value }))
                        }
                      />
                    </Field>
                  </div>
                </div>
              ))}
            </div>
          </div>
          )}

          {/* 项目管理人员 */}
          {levelOf('personnel') !== 'optional' && (
          <div id="profile-personnel">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-sm font-medium text-slate-200">
                项目管理人员
                {levelOf('personnel') === 'required' && (
                  <span className="ml-1 text-rose-500">*</span>
                )}
                {levelOf('personnel') === 'recommended' && (
                  <span className="ml-1 text-amber-400">建议</span>
                )}
                <span className="ml-2 text-xs text-slate-500">已填 {persons.length} 人</span>
                {liveMissingSet.has('personnel') && (
                  <span className="ml-2 text-xs text-amber-400">未填写</span>
                )}
              </h3>
              <Button
                variant="secondary"
                onClick={() => setPersons((rows) => [...rows, { ...emptyPerson }])}
              >
                + 添加人员
              </Button>
            </div>
            {persons.length === 0 && (
              <p className="text-xs text-slate-500">点「添加人员」录入项目经理等关键岗位</p>
            )}
            <div className="space-y-3">
              {persons.map((row, index) => (
                <div key={index} className="rounded-md border border-slate-700 p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs text-slate-400">人员 {index + 1}</span>
                    <button
                      type="button"
                      onClick={() => setPersons((rows) => rows.filter((_, i) => i !== index))}
                      className="text-xs text-rose-400 hover:underline"
                    >
                      删除
                    </button>
                  </div>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                    <Field label="岗位">
                      <input
                        className={INPUT_CLASS}
                        placeholder="如 项目经理"
                        value={row.position}
                        onChange={(e) =>
                          setPersons((rows) => updateRow(rows, index, { position: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="姓名">
                      <input
                        className={INPUT_CLASS}
                        value={row.name}
                        onChange={(e) =>
                          setPersons((rows) => updateRow(rows, index, { name: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="执业证书">
                      <input
                        className={INPUT_CLASS}
                        value={row.certificate}
                        onChange={(e) =>
                          setPersons((rows) => updateRow(rows, index, { certificate: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="安全证书">
                      <input
                        className={INPUT_CLASS}
                        value={row.safety_cert}
                        onChange={(e) =>
                          setPersons((rows) => updateRow(rows, index, { safety_cert: e.target.value }))
                        }
                      />
                    </Field>
                    <Field label="从业年限">
                      <input
                        className={INPUT_CLASS}
                        value={row.experience_years}
                        onChange={(e) =>
                          setPersons((rows) => updateRow(rows, index, { experience_years: e.target.value }))
                        }
                      />
                    </Field>
                  </div>
                </div>
              ))}
            </div>
          </div>
          )}

          {/* 财务数据 */}
          {levelOf('financials') !== 'optional' && (
          <div id="profile-financials">
            <h3 className="mb-2 text-sm font-medium text-slate-200">
              财务数据
              {levelOf('financials') === 'required' && (
                <span className="ml-1 text-rose-500">*</span>
              )}
              {levelOf('financials') === 'recommended' && (
                <span className="ml-1 text-amber-400">建议</span>
              )}
              {liveMissingSet.has('financials') && (
                <span className="ml-2 text-xs text-amber-400">未填写</span>
              )}
            </h3>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <Field label="报告期">
                <input
                  className={INPUT_CLASS}
                  placeholder="如 2024 年度（经审计）"
                  value={financials.report_period}
                  onChange={(e) => setFinancials((f) => ({ ...f, report_period: e.target.value }))}
                />
              </Field>
              <Field label="资产总额">
                <input
                  className={INPUT_CLASS}
                  value={financials.total_assets}
                  onChange={(e) => setFinancials((f) => ({ ...f, total_assets: e.target.value }))}
                />
              </Field>
              <Field label="负债总额">
                <input
                  className={INPUT_CLASS}
                  value={financials.total_liabilities}
                  onChange={(e) => setFinancials((f) => ({ ...f, total_liabilities: e.target.value }))}
                />
              </Field>
              <Field label="所有者权益">
                <input
                  className={INPUT_CLASS}
                  value={financials.owner_equity}
                  onChange={(e) => setFinancials((f) => ({ ...f, owner_equity: e.target.value }))}
                />
              </Field>
              <Field label="营业收入">
                <input
                  className={INPUT_CLASS}
                  value={financials.operating_revenue}
                  onChange={(e) => setFinancials((f) => ({ ...f, operating_revenue: e.target.value }))}
                />
              </Field>
              <Field label="净利润">
                <input
                  className={INPUT_CLASS}
                  value={financials.net_profit}
                  onChange={(e) => setFinancials((f) => ({ ...f, net_profit: e.target.value }))}
                />
              </Field>
              <Field label="经营活动现金流">
                <input
                  className={INPUT_CLASS}
                  value={financials.operating_cash_flow}
                  onChange={(e) =>
                    setFinancials((f) => ({ ...f, operating_cash_flow: e.target.value }))
                  }
                />
              </Field>
              <Field label="流动比率">
                <input
                  className={INPUT_CLASS}
                  value={financials.current_ratio}
                  onChange={(e) => setFinancials((f) => ({ ...f, current_ratio: e.target.value }))}
                />
              </Field>
              <Field label="速动比率">
                <input
                  className={INPUT_CLASS}
                  value={financials.quick_ratio}
                  onChange={(e) => setFinancials((f) => ({ ...f, quick_ratio: e.target.value }))}
                />
              </Field>
              <Field label="资产负债率">
                <input
                  className={INPUT_CLASS}
                  value={financials.debt_ratio}
                  onChange={(e) => setFinancials((f) => ({ ...f, debt_ratio: e.target.value }))}
                />
              </Field>
              <Field label="净资产收益率">
                <input
                  className={INPUT_CLASS}
                  value={financials.return_on_equity}
                  onChange={(e) =>
                    setFinancials((f) => ({ ...f, return_on_equity: e.target.value }))
                  }
                />
              </Field>
              <Field label="审计意见">
                <input
                  className={INPUT_CLASS}
                  value={financials.audit_opinion}
                  onChange={(e) => setFinancials((f) => ({ ...f, audit_opinion: e.target.value }))}
                />
              </Field>
            </div>
          </div>
          )}

          {/* 联系方式：始终展示，仅"电话"标为建议，其余子项不标记 */}
          <div id="profile-contact">
            <h3 className="mb-2 text-sm font-medium text-slate-200">
              联系方式
              {liveMissingSet.has('contact') && (
                <span className="ml-2 text-xs text-amber-400">未填写</span>
              )}
            </h3>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <Field label="通讯地址">
                <input
                  className={INPUT_CLASS}
                  value={contact.address}
                  onChange={(e) => setContact((c) => ({ ...c, address: e.target.value }))}
                />
              </Field>
              <Field label="邮政编码">
                <input
                  className={INPUT_CLASS}
                  value={contact.postal_code}
                  onChange={(e) => setContact((c) => ({ ...c, postal_code: e.target.value }))}
                />
              </Field>
              <Field label="电话（建议）" labelClass="text-amber-400">
                <input
                  className={INPUT_CLASS}
                  value={contact.phone}
                  onChange={(e) => setContact((c) => ({ ...c, phone: e.target.value }))}
                />
              </Field>
              <Field label="传真">
                <input
                  className={INPUT_CLASS}
                  value={contact.fax}
                  onChange={(e) => setContact((c) => ({ ...c, fax: e.target.value }))}
                />
              </Field>
              <Field label="电子邮箱">
                <input
                  className={INPUT_CLASS}
                  value={contact.email}
                  onChange={(e) => setContact((c) => ({ ...c, email: e.target.value }))}
                />
              </Field>
              <Field label="网址">
                <input
                  className={INPUT_CLASS}
                  value={contact.website}
                  onChange={(e) => setContact((c) => ({ ...c, website: e.target.value }))}
                />
              </Field>
              <Field label="开户银行">
                <input
                  className={INPUT_CLASS}
                  value={contact.bank}
                  onChange={(e) => setContact((c) => ({ ...c, bank: e.target.value }))}
                />
              </Field>
              <Field label="银行账号">
                <input
                  className={INPUT_CLASS}
                  value={contact.bank_account}
                  onChange={(e) => setContact((c) => ({ ...c, bank_account: e.target.value }))}
                />
              </Field>
            </div>
          </div>
        </div>

        {/* optional 字段折叠区：schema 标记为"标书未提及"的字段 */}
        {schema && (
          <div className="mt-4 border-t border-slate-800 pt-3">
            <button
              type="button"
              className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300"
              onClick={() => setOptionalOpen((v) => !v)}
            >
              <span>{optionalOpen ? '▼' : '▶'}</span>
              <span>
                {optionalOpen ? '收起' : '展开'} 标书未提及的可选字段
              </span>
              {!optionalOpen && (
                <span className="text-slate-600">
                  （如认证证书、完整联系方式等贵司情况可酌情补充）
                </span>
              )}
            </button>
            {optionalOpen && (
              <div className="mt-3 grid grid-cols-1 gap-4 rounded-md border border-dashed border-slate-700/50 bg-slate-800/20 p-3 md:grid-cols-2">
                {(['company_name','legal_representative','registered_capital','established_date','address','qualifications','certifications'] as const).map(
                  (key) => {
                    if (levelOf(key) !== 'optional') return null
                    const item = schemaItemOf(key)
                    const hint = item?.matched ? `匹配到：${item.reason}` : item?.reason ?? ''
                    return (
                      <div key={key} className="text-xs text-slate-500">
                        <span className="text-slate-400">{item?.label ?? key}</span>
                        {hint && (
                          <span className="ml-2 text-slate-600">· {hint}</span>
                        )}
                      </div>
                    )
                  },
                )}
                {(['similar_projects','personnel','financials'] as const).map(
                  (key) => {
                    if (levelOf(key) !== 'optional') return null
                    const item = schemaItemOf(key)
                    const hint = item?.reason ?? ''
                    return (
                      <div key={key} className="text-xs text-slate-500">
                        <span className="text-slate-400">{item?.label ?? key}</span>
                        {hint && (
                          <span className="ml-2 text-slate-600">· {hint}</span>
                        )}
                      </div>
                    )
                  },
                )}
              </div>
            )}
          </div>
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

      <Modal open={precheckOpen} title="投标人资料不完整" onClose={() => setPrecheckOpen(false)}>
        <div className="space-y-2 text-sm text-slate-300">
          <p>
            以下 {missingFields.length} 项资料未填写，生成后这些位置会留下【待填写】占位符。点击任意一项可跳到对应填写位置。
          </p>
          <ul className="space-y-1">
            {missingFields.map(({ key, label }) => {
              const isAdvanced = ADVANCED_FIELD_KEYS.has(String(key))
              return (
                <li
                  key={String(key)}
                  className="flex items-center justify-between gap-3 rounded-md border border-slate-700 px-3 py-1.5"
                >
                  <span className="text-amber-400">{label}</span>
                  <button
                    type="button"
                    onClick={() => goToField(key)}
                    className="text-xs text-sky-400 hover:underline"
                  >
                    去填写（{isAdvanced ? '下方资料区块' : '上方输入框'}）
                  </button>
                </li>
              )
            })}
          </ul>
          <p className="text-xs text-slate-500">
            也可以从「资料库」一键载入已保存的资料；或者跳过，生成后再补写。
          </p>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setPrecheckOpen(false)}>
            关闭
          </Button>
          <Button
            onClick={() => {
              setPrecheckOpen(false)
              setConfirmOpen(true)
            }}
          >
            仍然继续
          </Button>
        </div>
      </Modal>
    </div>
  )
}
