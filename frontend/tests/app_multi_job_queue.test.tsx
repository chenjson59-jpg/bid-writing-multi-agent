/**
 * T-034 多任务排队与全局任务栏组件测试（App 全链路，离线夹具）。
 *
 * 约定（同 T-030/T-031/T-035）：
 * - vi.mock 接管整个 api 模块，全部夹具离线返回，禁止真实网络；
 * - vi.useFakeTimers + advanceTimersByTimeAsync 驱动 1.5s 全局轮询，禁止真实 sleep；
 * - 交互统一用 fireEvent 派发真实 DOM 事件（假定时器下 user-event 死等）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, within } from '@testing-library/react'

vi.mock('../src/api', () => ({
  describeError: (e: unknown) => (e instanceof Error ? e.message : String(e)),
  apiUrl: vi.fn(() => '/api'),
  getHealth: vi.fn(),
  listDocuments: vi.fn(),
  getMarkdown: vi.fn(),
  getExtraction: vi.fn(),
  saveExtraction: vi.fn(),
  uploadParse: vi.fn(),
  getJob: vi.fn(),
  createBid: vi.fn(),
  listBids: vi.fn(),
  listTasks: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
  deleteBid: vi.fn(),
  deleteDocument: vi.fn(),
  createSupplement: vi.fn(),
  getBid: vi.fn(),
  listSections: vi.fn(),
  getSection: vi.fn(),
  saveSection: vi.fn(),
  rewriteSections: vi.fn(),
  sectionFileUrl: vi.fn(() => ''),
  listProfiles: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  getProfileSchema: vi.fn(),
  bidFileUrl: vi.fn(() => ''),
}))

import * as api from '../src/api'
import App from '../src/App'
import type { BidHistoryItem, BidSummary, JobView, ParseAccepted, SectionInfo } from '../src/types'

function mkJob(overrides: Partial<JobView> = {}): JobView {
  return {
    job_id: overrides.job_id ?? 'job-11111111',
    kind: overrides.kind ?? 'bid',
    status: overrides.status ?? 'running',
    created_at: overrides.created_at ?? null,
    started_at: overrides.started_at ?? null,
    finished_at: overrides.finished_at ?? null,
    options: overrides.options ?? {},
    source_name: overrides.source_name ?? 'demo.pdf',
    progress: overrides.progress ?? ['执行中'],
    result: overrides.result ?? null,
    extraction: overrides.extraction ?? null,
    ingest: overrides.ingest ?? null,
    bid: overrides.bid ?? null,
    error: overrides.error ?? null,
    error_type: overrides.error_type ?? null,
  }
}

function mkBidSummary(overrides: Partial<BidSummary> = {}): BidSummary {
  return {
    doc_id: 'doc-1',
    project_name: '演示项目',
    bidder: '演示投标人',
    total_sections: 1,
    total_words: 128,
    placeholder_count: 0,
    format_items_total: 0,
    format_items_covered: 0,
    evaluation_factors_total: 1,
    evaluation_factors_covered: 0,
    uncovered_factors: ['未覆盖因素 A'],
    missing_profile_fields: [],
    sections: [
      {
        id: 's1',
        title: '第一章',
        order: 1,
        strategy: 'generate',
        score_before: 60,
        score_after: 80,
        revisions: 0,
        word_count: 100,
        placeholder_count: 0,
        retrieved_count: 2,
        scoring_factors: ['因素 A'],
      },
    ],
    warnings: [],
    output_dir: 'upload/bids/bid-0001',
    markdown_path: 'bid.md',
    docx_path: 'bid.docx',
    report_path: 'report.json',
    ...overrides,
  }
}

function mkSection(overrides: Partial<SectionInfo> = {}): SectionInfo {
  return {
    id: 's1',
    title: '第一章',
    order: 1,
    strategy: 'generate',
    locked: false,
    word_count: 100,
    placeholder_count: 0,
    score_before: 60,
    score_after: 80,
    revisions: 0,
    ...overrides,
  }
}

const bidAccepted = (jobId: string): ParseAccepted => ({
  job_id: jobId,
  doc_id: 'doc-1',
  kind: 'bid',
  status: 'pending',
  job_url: `/api/jobs/${jobId}`,
})

const doc = {
  doc_id: 'doc-1',
  source_name: 'demo.pdf',
  doc_type: 'tender_file',
  parser: 'auto',
  pages: 10,
  sections: 3,
  has_extraction: true,
  extraction_reviewed: false,
  parsed_at: null,
}

const health = {
  status: 'ok',
  version: '1.0.0',
  task_backend: 'local',
  broker_ok: null,
  worker_ok: true,
  queue_depth: null,
}

async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
  })
}

/** 编写页走完「提交编写 → 资料不完整仍然继续 → 确认」弹窗链路。 */
async function submitBid(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: '提交编写' }))
  fireEvent.click(screen.getByRole('button', { name: '仍然继续' }))
  fireEvent.click(screen.getByRole('button', { name: '确认' }))
  await flushMicrotasks()
}

/** 任务进行中返回表单（T-034：不关闭任务、不停止轮询），以便再次提交。 */
async function backToForm(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: '返回表单（后台继续）' }))
  await flushMicrotasks()
}

/** 任务栏中按来源名定位某一行。 */
function taskRow(name: string): HTMLElement {
  const list = screen.getByRole('list')
  return within(list).getByText(name).closest('li') as HTMLElement
}

describe('T-034 多任务排队与全局任务栏（App 全链路，离线夹具）', () => {
  let jobStore: Record<string, JobView>

  beforeEach(() => {
    vi.useFakeTimers()
    jobStore = {}
    vi.mocked(api.getHealth).mockResolvedValue(health)
    vi.mocked(api.listDocuments).mockResolvedValue([doc])
    vi.mocked(api.listBids).mockResolvedValue([])
    vi.mocked(api.listTasks).mockResolvedValue([])
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.getProfileSchema).mockRejectedValue(new Error('no schema'))
    vi.mocked(api.listSections).mockResolvedValue([mkSection()])
    vi.mocked(api.getBid).mockResolvedValue({
      bid_id: 'bid-0001',
      generated_at: null,
      summary: mkBidSummary(),
    } satisfies BidHistoryItem)
    vi.mocked(api.createBid).mockResolvedValue(bidAccepted('job-bid-1'))
    vi.mocked(api.createSupplement).mockResolvedValue({
      job_id: 'job-sup-1',
      doc_id: 'doc-1',
      kind: 'supplement',
      status: 'pending',
      job_url: '/api/jobs/job-sup-1',
    })
    vi.mocked(api.rewriteSections).mockResolvedValue({
      job_id: 'job-rw-1',
      doc_id: 'doc-1',
      kind: 'rewrite',
      status: 'pending',
      job_url: '/api/jobs/job-rw-1',
    })
    vi.mocked(api.getJob).mockImplementation(async (jobId: string) => {
      const job = jobStore[jobId]
      if (!job) throw new Error(`unknown job ${jobId}`)
      return job
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('连续提交两个同类编写任务：互不覆盖、两个 jobId 都被轮询、任务栏可切换查看', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'running',
      source_name: 'first.pdf',
      progress: ['第一个任务进度'],
    })
    jobStore['job-bid-2'] = mkJob({
      job_id: 'job-bid-2',
      kind: 'bid',
      status: 'pending',
      source_name: 'second.pdf',
      progress: ['第二个任务进度'],
    })
    vi.mocked(api.createBid)
      .mockResolvedValueOnce(bidAccepted('job-bid-1'))
      .mockResolvedValueOnce(bidAccepted('job-bid-2'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()

    await submitBid()
    await advance(1600)
    // 第一个任务后台继续，返回表单后再次提交第二个（全程不刷新、不关闭任务）
    await backToForm()
    await submitBid()
    await advance(1600)

    // 两个任务同时出现在底部任务栏，互不覆盖
    expect(taskRow('first.pdf')).toBeInTheDocument()
    expect(taskRow('second.pdf')).toBeInTheDocument()
    const polledIds = vi.mocked(api.getJob).mock.calls.map((call) => call[0])
    expect(polledIds).toContain('job-bid-1')
    expect(polledIds).toContain('job-bid-2')

    // 后提交的 job-bid-2 自动选中（页面任务卡显示其进度），任务栏置顶
    const rows = screen.getAllByRole('listitem')
    expect(within(rows[0]).getByText('second.pdf')).toBeInTheDocument()
    const taskCard = screen.getByTestId('bid-task-card')
    expect(within(taskCard).getByText('第二个任务进度')).toBeInTheDocument()

    // 任务栏「查看」切回第一个任务
    fireEvent.click(within(taskRow('first.pdf')).getByRole('button', { name: '查看' }))
    expect(within(screen.getByTestId('bid-task-card')).getByText('第一个任务进度')).toBeInTheDocument()
    expect(within(screen.getByTestId('bid-task-card')).queryByText('第二个任务进度')).toBeNull()
  })

  it('pending/running 在任务栏文案区分（排队中 vs 运行中）', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'running',
      source_name: 'running.pdf',
    })
    jobStore['job-bid-2'] = mkJob({
      job_id: 'job-bid-2',
      kind: 'bid',
      status: 'pending',
      source_name: 'pending.pdf',
    })
    vi.mocked(api.createBid)
      .mockResolvedValueOnce(bidAccepted('job-bid-1'))
      .mockResolvedValueOnce(bidAccepted('job-bid-2'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)
    await backToForm()
    await submitBid()
    await advance(1600)

    // 两行分别展示运行中 / 排队中，颜色文案均不同
    expect(within(taskRow('running.pdf')).getByText('运行中')).toBeInTheDocument()
    expect(within(taskRow('pending.pdf')).getByText('排队中')).toBeInTheDocument()
    // 头部计数提示包含排队数量（与行内「排队中」区分，精确匹配头部文案）
    expect(screen.getByText(/· 排队 1/)).toBeInTheDocument()
  })

  it('任务终态后停止该任务轮询、快照留存，显式「关闭」后从集合移除', async () => {
    jobStore['job-bid-1'] = mkJob({ job_id: 'job-bid-1', kind: 'bid', status: 'running' })

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)
    expect(screen.getByText('编写进度')).toBeInTheDocument()

    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    await advance(1600)
    expect(screen.getByRole('heading', { name: '演示项目' })).toBeInTheDocument()

    // 终态后该 jobId 的 getJob 调用次数冻结
    const callsAtTerminal = vi.mocked(api.getJob).mock.calls.filter((c) => c[0] === 'job-bid-1').length
    await advance(5000)
    const callsAfterWait = vi.mocked(api.getJob).mock.calls.filter((c) => c[0] === 'job-bid-1').length
    expect(callsAfterWait).toBe(callsAtTerminal)

    // 终态行留存并出现「关闭」，点击后集合移除、页面回表单
    const row = screen.getAllByText('demo.pdf')[0].closest('li') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: '关闭' }))
    expect(screen.queryByRole('button', { name: '关闭' })).toBeNull()
    expect(screen.getByRole('button', { name: '提交编写' })).toBeInTheDocument()
  })

  it('补写任务进入全局任务栏（标签「补写」），终态 onSettled 回调只触发一次列表刷新', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    vi.mocked(api.createBid).mockResolvedValue(bidAccepted('job-bid-1'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)

    // ResultView 内发起补写
    jobStore['job-sup-1'] = mkJob({
      job_id: 'job-sup-1',
      kind: 'supplement',
      status: 'running',
      source_name: 'bid-0001',
    })
    fireEvent.click(screen.getByRole('button', { name: '补写未覆盖因素' }))
    await flushMicrotasks()
    await advance(1600)

    const supRow = taskRow('bid-0001')
    expect(within(supRow).getByText('补写')).toBeInTheDocument()
    expect(within(supRow).queryByRole('button', { name: '关闭' })).toBeNull()

    // 停留在编写页：终态迁移点触发一次性 onSettled → onSupplemented → onWritten（侧栏计数刷新）
    const listBidsBeforeTerminal = vi.mocked(api.listBids).mock.calls.length
    jobStore['job-sup-1'] = mkJob({
      job_id: 'job-sup-1',
      kind: 'supplement',
      status: 'succeeded',
      source_name: 'bid-0001',
      bid: mkBidSummary({ output_dir: 'upload/bids/bid-0002', supplement_of: 'bid-0001' }),
    })
    await advance(1600)
    expect(vi.mocked(api.listBids).mock.calls.length).toBe(listBidsBeforeTerminal + 1)
    expect(screen.getByText(/补写完成/)).toBeInTheDocument()

    // 重放/继续推进不产生第二次回调（take 即删 + 终态→终态无迁移，双保险）
    await advance(5000)
    expect(vi.mocked(api.listBids).mock.calls.length).toBe(listBidsBeforeTerminal + 1)

    // 切到历史页（挂载即产生一次列表请求）；无新的终态迁移时 nonce 不触发额外刷新
    fireEvent.click(screen.getByRole('button', { name: /任务与产物/ }))
    await flushMicrotasks()
    const bidsAtMount = vi.mocked(api.listBids).mock.calls.length
    const tasksAtMount = vi.mocked(api.listTasks).mock.calls.length
    await advance(3200)
    expect(vi.mocked(api.listBids).mock.calls.length).toBe(bidsAtMount)
    expect(vi.mocked(api.listTasks).mock.calls.length).toBe(tasksAtMount)
  })

  it('重写任务进入全局任务栏（标签「重写」），终态只触发一次章节/摘要刷新', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    vi.mocked(api.createBid).mockResolvedValue(bidAccepted('job-bid-1'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)

    const intervalSpy = vi.spyOn(window, 'setInterval')
    jobStore['job-rw-1'] = mkJob({
      job_id: 'job-rw-1',
      kind: 'rewrite',
      status: 'running',
      source_name: 'bid-0001',
    })
    // 单章重写按钮（行内），不走 window.confirm
    fireEvent.click(screen.getByRole('button', { name: '重写' }))
    await flushMicrotasks()
    await advance(1600)

    // 不允许复活 2s 私有 interval：全局只有 1500ms poller 与 5000ms 健康检查
    const delays = intervalSpy.mock.calls.map((call) => call[1])
    expect(delays.every((delay) => delay === 1500 || delay === 5000)).toBe(true)
    intervalSpy.mockRestore()

    const rwRow = taskRow('bid-0001')
    expect(within(rwRow).getByText('重写')).toBeInTheDocument()

    const getBidBeforeTerminal = vi.mocked(api.getBid).mock.calls.length
    jobStore['job-rw-1'] = mkJob({
      job_id: 'job-rw-1',
      kind: 'rewrite',
      status: 'succeeded',
      source_name: 'bid-0001',
      bid: mkBidSummary(),
    })
    await advance(1600)

    // 终态回调刷新摘要（getBid 恰好一次）与章节列表
    expect(vi.mocked(api.getBid).mock.calls.length).toBe(getBidBeforeTerminal + 1)
    const listSectionsAtTerminal = vi.mocked(api.listSections).mock.calls.length
    await advance(5000)
    expect(vi.mocked(api.getBid).mock.calls.length).toBe(getBidBeforeTerminal + 1)
    expect(vi.mocked(api.listSections).mock.calls.length).toBe(listSectionsAtTerminal)
  })

  it('T-036 补写终态行关闭后归位 closed，再次发起可完整走 running → done', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    vi.mocked(api.createBid).mockResolvedValue(bidAccepted('job-bid-1'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)

    jobStore['job-sup-1'] = mkJob({
      job_id: 'job-sup-1',
      kind: 'supplement',
      status: 'running',
      source_name: 'bid-0001',
    })
    fireEvent.click(screen.getByRole('button', { name: '补写未覆盖因素' }))
    await flushMicrotasks()
    await advance(1600)

    jobStore['job-sup-1'] = mkJob({
      job_id: 'job-sup-1',
      kind: 'supplement',
      status: 'succeeded',
      source_name: 'bid-0001',
      bid: mkBidSummary({ output_dir: 'upload/bids/bid-0002', supplement_of: 'bid-0001' }),
    })
    await advance(1600)
    expect(screen.getByText(/补写完成/)).toBeInTheDocument()

    fireEvent.click(within(taskRow('bid-0001')).getByRole('button', { name: '关闭' }))
    await flushMicrotasks()

    expect(screen.queryByText('已提交补写任务，正在等待新章节生成…')).toBeNull()
    expect(screen.queryByText(/补写完成/)).toBeNull()
    expect(screen.getByText('补写任务已从任务栏关闭，可重新发起补写')).toBeInTheDocument()
    const closedButton = screen.getByRole('button', { name: '补写未覆盖因素' })
    expect(closedButton).toBeEnabled()
    expect(within(closedButton).queryByRole('status')).toBeNull()

    jobStore['job-sup-2'] = mkJob({
      job_id: 'job-sup-2',
      kind: 'supplement',
      status: 'running',
      source_name: 'bid-0001',
    })
    vi.mocked(api.createSupplement).mockResolvedValueOnce({
      job_id: 'job-sup-2',
      doc_id: 'doc-1',
      kind: 'supplement',
      status: 'pending',
      job_url: '/api/jobs/job-sup-2',
    })
    fireEvent.click(closedButton)
    await flushMicrotasks()

    expect(closedButton).toBeDisabled()
    expect(within(closedButton).getByRole('status')).toBeInTheDocument()
    expect(screen.getByText('已提交补写任务，正在等待新章节生成…')).toBeInTheDocument()

    await advance(1600)
    jobStore['job-sup-2'] = mkJob({
      job_id: 'job-sup-2',
      kind: 'supplement',
      status: 'succeeded',
      source_name: 'bid-0001',
      bid: mkBidSummary({ output_dir: 'upload/bids/bid-0003', supplement_of: 'bid-0001' }),
    })
    await advance(1600)

    expect(closedButton).toBeEnabled()
    expect(within(closedButton).queryByRole('status')).toBeNull()
    expect(screen.getByText(/补写完成/)).toBeInTheDocument()
    expect(screen.queryByText('补写任务已从任务栏关闭，可重新发起补写')).toBeNull()
  })

  it('T-036 重写终态行关闭后归位 closed，再次发起可完整走 running → done', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    vi.mocked(api.createBid).mockResolvedValue(bidAccepted('job-bid-1'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)

    jobStore['job-rw-1'] = mkJob({
      job_id: 'job-rw-1',
      kind: 'rewrite',
      status: 'running',
      source_name: 'bid-0001',
    })
    fireEvent.click(screen.getByRole('button', { name: '重写' }))
    await flushMicrotasks()
    await advance(1600)

    jobStore['job-rw-1'] = mkJob({
      job_id: 'job-rw-1',
      kind: 'rewrite',
      status: 'succeeded',
      source_name: 'bid-0001',
      bid: mkBidSummary(),
    })
    await advance(1600)
    expect(screen.getByText('章节重写完成，产物已原地更新')).toBeInTheDocument()

    fireEvent.click(within(taskRow('bid-0001')).getByRole('button', { name: '关闭' }))
    await flushMicrotasks()

    expect(screen.queryByText(/已提交.*重写/)).toBeNull()
    expect(screen.queryByText('章节重写完成，产物已原地更新')).toBeNull()
    expect(screen.getByText('重写任务已从任务栏关闭，可重新发起重写')).toBeInTheDocument()
    const closedBatchButton = screen.getByRole('button', { name: '重写全部未锁定章节' })
    const closedInlineButtons = screen.getAllByRole('button', { name: '重写' })
    expect(closedBatchButton).toBeEnabled()
    expect(within(closedBatchButton).queryByRole('status')).toBeNull()
    expect(closedInlineButtons).toHaveLength(1)
    closedInlineButtons.forEach((button) => {
      expect(button).toBeEnabled()
      expect(within(button).queryByRole('status')).toBeNull()
    })

    jobStore['job-rw-2'] = mkJob({
      job_id: 'job-rw-2',
      kind: 'rewrite',
      status: 'running',
      source_name: 'bid-0001',
    })
    vi.mocked(api.rewriteSections).mockResolvedValueOnce({
      job_id: 'job-rw-2',
      doc_id: 'doc-1',
      kind: 'rewrite',
      status: 'pending',
      job_url: '/api/jobs/job-rw-2',
    })
    fireEvent.click(closedInlineButtons[0])
    await flushMicrotasks()

    expect(closedBatchButton).toBeDisabled()
    expect(within(closedBatchButton).getByRole('status')).toBeInTheDocument()
    closedInlineButtons.forEach((button) => {
      expect(button).toBeDisabled()
    })
    expect(screen.getByText('已提交单章重写任务…')).toBeInTheDocument()

    await advance(1600)
    jobStore['job-rw-2'] = mkJob({
      job_id: 'job-rw-2',
      kind: 'rewrite',
      status: 'succeeded',
      source_name: 'bid-0001',
      bid: mkBidSummary(),
    })
    await advance(1600)

    expect(screen.getByText('章节重写完成，产物已原地更新')).toBeInTheDocument()
    expect(closedBatchButton).toBeEnabled()
    expect(closedInlineButtons[0]).toBeEnabled()
    expect(screen.queryByText('重写任务已从任务栏关闭，可重新发起重写')).toBeNull()
  })

  it('ResultView 卸载后任务终态：回调不触达已卸载组件（无 getBid 刷新），重挂不报错', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    vi.mocked(api.createBid).mockResolvedValue(bidAccepted('job-bid-1'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)

    jobStore['job-rw-1'] = mkJob({
      job_id: 'job-rw-1',
      kind: 'rewrite',
      status: 'running',
      source_name: 'bid-0001',
    })
    fireEvent.click(screen.getByRole('button', { name: '重写' }))
    await flushMicrotasks()
    await advance(1600)
    expect(taskRow('bid-0001')).toBeInTheDocument()

    // 切走 tab：WritePage/ResultView 卸载，重写任务仍在全局集合中轮询
    fireEvent.click(screen.getByRole('button', { name: /上传解析/ }))
    await flushMicrotasks()
    const getBidAtUnmount = vi.mocked(api.getBid).mock.calls.length

    jobStore['job-rw-1'] = mkJob({
      job_id: 'job-rw-1',
      kind: 'rewrite',
      status: 'succeeded',
      source_name: 'bid-0001',
      bid: mkBidSummary(),
    })
    await advance(3200)

    // 已卸载实例的 refreshSummary 不执行：getBid 次数不增长
    expect(vi.mocked(api.getBid).mock.calls.length).toBe(getBidAtUnmount)
    // 任务在任务栏终态留存（全局 poller 仍在跟踪）
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    expect(screen.getByRole('heading', { name: '演示项目' })).toBeInTheDocument()
  })

  it('多任务结构下回归：切 tab 任务视图恢复，「查看」跨 tab 跳转，历史页终态自动刷新', async () => {
    jobStore['job-parse-1'] = mkJob({
      job_id: 'job-parse-1',
      kind: 'parse',
      status: 'running',
      source_name: 'parse.pdf',
      progress: ['解析文档', '结构化抽取'],
    })
    vi.mocked(api.uploadParse).mockResolvedValue({
      job_id: 'job-parse-1',
      doc_id: 'doc-1',
      kind: 'parse',
      status: 'pending',
      job_url: '/api/jobs/job-parse-1',
    })

    render(<App />)
    await flushMicrotasks()
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, {
      target: { files: [new File(['pdf-bytes'], 'parse.pdf', { type: 'application/pdf' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始解析' }))
    await flushMicrotasks()
    await advance(1600)
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()

    // 切到编写页再切回：任务视图受控恢复
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    expect(screen.queryByTestId('parse-task-card')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /上传解析/ }))
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()

    // 跨 tab 点「查看」：从编写页跳回上传页任务卡片
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    fireEvent.click(within(taskRow('parse.pdf')).getByRole('button', { name: '查看' }))
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()

    // 终态迁移驱动历史页自动刷新一次
    fireEvent.click(screen.getByRole('button', { name: /任务与产物/ }))
    await flushMicrotasks()
    const bidsAtMount = vi.mocked(api.listBids).mock.calls.length
    jobStore['job-parse-1'] = mkJob({
      job_id: 'job-parse-1',
      kind: 'parse',
      status: 'succeeded',
      source_name: 'parse.pdf',
      result: {
        doc_id: 'doc-1',
        doc_type: 'tender_file',
        parser: 'auto',
        pages: 10,
        sections: 3,
        source_name: 'parse.pdf',
      },
    })
    await advance(1600)
    expect(vi.mocked(api.listBids).mock.calls.length).toBe(bidsAtMount + 1)
  })

  it('页面复位只关闭当前选中任务：集合内其他任务保留并仍可查看', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'running',
      source_name: 'first.pdf',
      progress: ['第一个任务进度'],
    })
    jobStore['job-bid-2'] = mkJob({
      job_id: 'job-bid-2',
      kind: 'bid',
      status: 'running',
      source_name: 'second.pdf',
      progress: ['第二个任务进度'],
    })
    vi.mocked(api.createBid)
      .mockResolvedValueOnce(bidAccepted('job-bid-1'))
      .mockResolvedValueOnce(bidAccepted('job-bid-2'))

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: /编写标书/ }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)
    await backToForm()
    await submitBid()
    await advance(1600)
    // 两个任务都在任务栏；切回第一个并等它终态
    fireEvent.click(within(taskRow('first.pdf')).getByRole('button', { name: '查看' }))
    expect(within(screen.getByTestId('bid-task-card')).getByText('第一个任务进度')).toBeInTheDocument()
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      source_name: 'first.pdf',
      bid: mkBidSummary(),
    })
    await advance(1600)
    expect(screen.getByRole('heading', { name: '演示项目' })).toBeInTheDocument()

    // 「再写一份」只关闭当前选中的第一个任务
    fireEvent.click(screen.getByRole('button', { name: '再写一份' }))
    expect(screen.getByRole('button', { name: '提交编写' })).toBeInTheDocument()
    expect(screen.queryByText('first.pdf')).toBeNull()
    expect(taskRow('second.pdf')).toBeInTheDocument()

    // 第二个任务仍可查看且进度不丢
    fireEvent.click(within(taskRow('second.pdf')).getByRole('button', { name: '查看' }))
    expect(within(screen.getByTestId('bid-task-card')).getByText('第二个任务进度')).toBeInTheDocument()
  })
})