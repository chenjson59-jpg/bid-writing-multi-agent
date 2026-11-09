/**
 * T-030 任务终态生命周期组件测试：结果留存、显式复位、历史页事件驱动刷新。
 *
 * 约定（同 T-031/T-035）：
 * - vi.mock 接管整个 api 模块，全部夹具离线返回，禁止真实网络；
 * - vi.useFakeTimers + advanceTimersByTimeAsync 驱动 1.5s 轮询，禁止真实 sleep；
 * - 交互统一用 fireEvent 派发真实 DOM 事件（假定时器下 user-event 会死等）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'

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
import type { BidSummary, JobView, ParseAccepted } from '../src/types'

function mkJob(overrides: Partial<JobView> = {}): JobView {
  return {
    job_id: overrides.job_id ?? 'job-11111111',
    kind: overrides.kind ?? 'parse',
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
    evaluation_factors_total: 0,
    evaluation_factors_covered: 0,
    uncovered_factors: [],
    missing_profile_fields: [],
    sections: [],
    warnings: [],
    output_dir: 'upload/bids/doc-1-20261109',
    markdown_path: 'bid.md',
    docx_path: 'bid.docx',
    report_path: 'report.json',
    ...overrides,
  }
}

const parseAccepted = (jobId: string): ParseAccepted => ({
  job_id: jobId,
  doc_id: 'doc-1',
  kind: 'parse',
  status: 'pending',
  job_url: `/api/jobs/${jobId}`,
})

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

/** 推进定时器并刷掉 interval 回调里的 await/Promise 微任务。 */
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

/** 上传页选择 PDF 并提交解析任务。 */
async function submitParse(): Promise<void> {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, {
    target: { files: [new File(['pdf-bytes'], 'demo.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(screen.getByRole('button', { name: '开始解析' }))
  await flushMicrotasks()
}

/** 编写页走完「提交编写 → 资料不完整仍然继续 → 确认」弹窗链路。 */
async function submitBid(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: '提交编写' }))
  fireEvent.click(screen.getByRole('button', { name: '仍然继续' }))
  fireEvent.click(screen.getByRole('button', { name: '确认' }))
  await flushMicrotasks()
}

describe('T-030 终态生命周期（App 全链路，离线夹具）', () => {
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
    vi.mocked(api.listSections).mockResolvedValue([])
    vi.mocked(api.uploadParse).mockResolvedValue(parseAccepted('job-parse-1'))
    vi.mocked(api.createBid).mockResolvedValue(bidAccepted('job-bid-1'))
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

  it('编写成功后推进超过 8 秒 ResultView 仍在（旧实现 8s grace 会抹掉），且终态后停止轮询', async () => {
    jobStore['job-bid-1'] = mkJob({ job_id: 'job-bid-1', kind: 'bid', status: 'running' })

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: '编写标书' }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)
    expect(screen.getByText('编写进度')).toBeInTheDocument()

    // 进入终态：ResultView 出现，底部任务栏（仅运行中任务）消失
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    await advance(1600)
    expect(screen.getByRole('heading', { name: '演示项目' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '再写一份' })).toBeInTheDocument()

    // 终态观测后 getJob 不再被调用：记录当前次数，推进多个轮询周期
    const callsAtTerminal = vi.mocked(api.getJob).mock.calls.length
    await advance(5000)
    expect(vi.mocked(api.getJob).mock.calls.length).toBe(callsAtTerminal)

    // 推进超过旧 grace 的 8 秒，结果视图仍在、表单不恢复
    await advance(5000)
    expect(screen.getByRole('heading', { name: '演示项目' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '提交编写' })).toBeNull()
    expect(vi.mocked(api.getJob).mock.calls.length).toBe(callsAtTerminal)
  })

  it('点「再写一份」回表单并可再次提交，新任务视图正常出现（全程无需刷新）', async () => {
    jobStore['job-bid-1'] = mkJob({ job_id: 'job-bid-1', kind: 'bid', status: 'running' })

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: '编写标书' }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'succeeded',
      bid: mkBidSummary(),
    })
    await advance(1600)
    expect(screen.getByRole('heading', { name: '演示项目' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '再写一份' }))
    expect(screen.queryByRole('heading', { name: '演示项目' })).toBeNull()
    expect(screen.getByRole('button', { name: '提交编写' })).toBeInTheDocument()
    expect(vi.mocked(api.createBid)).toHaveBeenCalledTimes(1)

    // 第二次提交：新 jobId，首帧前显示提交中，poll 返回后恢复任务视图
    vi.mocked(api.createBid).mockResolvedValueOnce(bidAccepted('job-bid-2'))
    jobStore['job-bid-2'] = mkJob({ job_id: 'job-bid-2', kind: 'bid', status: 'running' })
    await submitBid()
    expect(vi.mocked(api.createBid)).toHaveBeenCalledTimes(2)
    expect(screen.getByText('提交中')).toBeInTheDocument()
    await advance(1600)
    expect(screen.getByText('编写进度')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '演示项目' })).toBeNull()
  })

  it('解析成功后「再上传一个」恢复文件选择区，可再次上传且成功回调能二次触发', async () => {
    jobStore['job-parse-1'] = mkJob({ job_id: 'job-parse-1', kind: 'parse', status: 'running' })

    render(<App />)
    await flushMicrotasks()
    await submitParse()
    await advance(1600)
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()

    jobStore['job-parse-1'] = mkJob({
      job_id: 'job-parse-1',
      kind: 'parse',
      status: 'succeeded',
      result: {
        doc_id: 'doc-1',
        doc_type: 'tender_file',
        parser: 'auto',
        pages: 10,
        sections: 3,
        source_name: 'demo.pdf',
      },
    })
    await advance(1600)
    expect(screen.getByText('解析结果')).toBeInTheDocument()
    // 首次成功触发 onUploaded → refreshCounts 调 listDocuments
    const listCallsAfterFirstSuccess = vi.mocked(api.listDocuments).mock.calls.length
    expect(listCallsAfterFirstSuccess).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: '再上传一个' }))
    expect(document.querySelector('input[type="file"]')).toBeTruthy()
    expect(screen.queryByText('解析结果')).toBeNull()

    // 第二次上传并成功：旧 fireRef 布尔闩锁会吞掉这次回调，修复后应再次刷新
    vi.mocked(api.uploadParse).mockResolvedValueOnce(parseAccepted('job-parse-2'))
    jobStore['job-parse-2'] = mkJob({ job_id: 'job-parse-2', kind: 'parse', status: 'running' })
    await submitParse()
    await advance(1600)
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()

    jobStore['job-parse-2'] = mkJob({
      job_id: 'job-parse-2',
      kind: 'parse',
      status: 'succeeded',
      result: {
        doc_id: 'doc-1',
        doc_type: 'tender_file',
        parser: 'auto',
        pages: 10,
        sections: 3,
        source_name: 'demo.pdf',
      },
    })
    await advance(1600)
    expect(vi.mocked(api.listDocuments).mock.calls.length).toBeGreaterThan(
      listCallsAfterFirstSuccess,
    )
    expect(vi.mocked(api.uploadParse)).toHaveBeenCalledTimes(2)
  })

  it('解析失败后提供「重新上传」显式出口（grace 移除后失败任务不再自动消失）', async () => {
    jobStore['job-parse-1'] = mkJob({
      job_id: 'job-parse-1',
      kind: 'parse',
      status: 'failed',
      error: '解析服务不可用',
    })

    render(<App />)
    await flushMicrotasks()
    await submitParse()
    await advance(1600)

    expect(screen.getByText('解析服务不可用')).toBeInTheDocument()
    // 推进超过旧 grace 时长，失败任务卡仍在
    await advance(8100)
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()
    expect(document.querySelector('input[type="file"]')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '重新上传' }))
    expect(document.querySelector('input[type="file"]')).toBeTruthy()
    expect(screen.queryByTestId('parse-task-card')).toBeNull()
  })

  it('HistoryPage 在任务终态后自动重新拉取列表；无任务时推进时间不产生空转请求', async () => {
    jobStore['job-parse-1'] = mkJob({ job_id: 'job-parse-1', kind: 'parse', status: 'running' })

    render(<App />)
    await flushMicrotasks()
    await submitParse()
    await advance(1600)
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()

    // 切到历史页：挂载首载一次
    fireEvent.click(screen.getByRole('button', { name: /任务与产物/ }))
    await flushMicrotasks()
    const bidsAtMount = vi.mocked(api.listBids).mock.calls.length
    const tasksAtMount = vi.mocked(api.listTasks).mock.calls.length
    expect(bidsAtMount).toBeGreaterThan(0)
    expect(tasksAtMount).toBeGreaterThan(0)

    // 后台任务进入终态（页面仍停在历史页）：App 终态事件驱动自动 reload
    jobStore['job-parse-1'] = mkJob({
      job_id: 'job-parse-1',
      kind: 'parse',
      status: 'succeeded',
      result: {
        doc_id: 'doc-1',
        doc_type: 'tender_file',
        parser: 'auto',
        pages: 10,
        sections: 3,
        source_name: 'demo.pdf',
      },
    })
    await advance(1600)
    expect(vi.mocked(api.listBids).mock.calls.length).toBe(bidsAtMount + 1)
    expect(vi.mocked(api.listTasks).mock.calls.length).toBe(tasksAtMount + 1)

    // 终态后已无活跃任务，继续推进 10 秒不得出现任何空转刷新
    await advance(10000)
    expect(vi.mocked(api.listBids).mock.calls.length).toBe(bidsAtMount + 1)
    expect(vi.mocked(api.listTasks).mock.calls.length).toBe(tasksAtMount + 1)
  })

  it('编写失败：「返回修改参数」回表单后可再次提交', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'failed',
      error: '模型调用失败',
    })

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: '编写标书' }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)

    expect(screen.getByText('编写失败')).toBeInTheDocument()
    expect(screen.getByText('模型调用失败')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '返回修改参数' }))
    expect(screen.getByRole('button', { name: '提交编写' })).toBeInTheDocument()
    expect(screen.queryByText('编写失败')).toBeNull()

    vi.mocked(api.createBid).mockResolvedValueOnce(bidAccepted('job-bid-2'))
    jobStore['job-bid-2'] = mkJob({ job_id: 'job-bid-2', kind: 'bid', status: 'running' })
    await submitBid()
    await advance(1600)
    expect(screen.getByText('编写进度')).toBeInTheDocument()
    expect(vi.mocked(api.createBid)).toHaveBeenCalledTimes(2)
  })

  it('编写失败：「重试（沿用原参数）」以相同 body 再次创建任务并恢复任务视图', async () => {
    jobStore['job-bid-1'] = mkJob({
      job_id: 'job-bid-1',
      kind: 'bid',
      status: 'failed',
      error: '模型调用失败',
    })

    render(<App />)
    await flushMicrotasks()
    fireEvent.click(screen.getByRole('button', { name: '编写标书' }))
    await flushMicrotasks()
    await submitBid()
    await advance(1600)
    expect(screen.getByText('编写失败')).toBeInTheDocument()
    const firstBody = vi.mocked(api.createBid).mock.calls[0][0]

    vi.mocked(api.createBid).mockResolvedValueOnce(bidAccepted('job-bid-2'))
    jobStore['job-bid-2'] = mkJob({ job_id: 'job-bid-2', kind: 'bid', status: 'running' })
    fireEvent.click(screen.getByRole('button', { name: '重试（沿用原参数）' }))
    await flushMicrotasks()

    expect(vi.mocked(api.createBid)).toHaveBeenCalledTimes(2)
    expect(vi.mocked(api.createBid).mock.calls[1][0]).toEqual(firstBody)
    await advance(1600)
    expect(screen.getByText('编写进度')).toBeInTheDocument()
    expect(screen.queryByText('编写失败')).toBeNull()
  })

  it('运行中 poll 单次失败：静默跳过且不误停轮询，网络恢复后终态仍可观测并留存', async () => {
    jobStore['job-parse-1'] = mkJob({ job_id: 'job-parse-1', kind: 'parse', status: 'running' })

    render(<App />)
    await flushMicrotasks()
    await submitParse()
    await advance(1600)
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()
    const callsBeforeFailure = vi.mocked(api.getJob).mock.calls.length

    // 下一轮 poll 网络抖动：静默跳过，任务视图保持，轮询不中断
    vi.mocked(api.getJob).mockRejectedValueOnce(new Error('network down'))
    await advance(1500)
    expect(screen.getByTestId('parse-task-card')).toBeInTheDocument()

    // 恢复后下一轮继续请求并观测到终态：结果卡出现，且之后不再发请求
    jobStore['job-parse-1'] = mkJob({
      job_id: 'job-parse-1',
      kind: 'parse',
      status: 'succeeded',
      result: {
        doc_id: 'doc-1',
        doc_type: 'tender_file',
        parser: 'auto',
        pages: 10,
        sections: 3,
        source_name: 'demo.pdf',
      },
    })
    await advance(1500)
    expect(screen.getByText('解析结果')).toBeInTheDocument()
    expect(vi.mocked(api.getJob).mock.calls.length).toBeGreaterThan(callsBeforeFailure)
    const callsAtTerminal = vi.mocked(api.getJob).mock.calls.length
    await advance(5000)
    expect(vi.mocked(api.getJob).mock.calls.length).toBe(callsAtTerminal)
  })
})