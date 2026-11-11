/**
 * T-031 活跃任务状态上移 App 层的组件测试。
 *
 * 约定：
 * - vi.mock 接管整个 api 模块，全部夹具离线返回，禁止真实网络；
 * - vi.useFakeTimers + advanceTimersByTimeAsync 驱动 1.5s 轮询，禁止真实 sleep；
 * - 交互统一用 fireEvent 派发真实 DOM 事件（不直接调 props 回调）：user-event v14 的
 *   click/upload 内部会排期 setTimeout，与 vi.useFakeTimers 存在死等，T-035 的
 *   user-event 写法保留给无假定时器的用例（见 ui.test.tsx）。
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
import { WritePage } from '../src/pages/WritePage'
import type { JobView } from '../src/types'

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
    progress: overrides.progress ?? ['解析文档'],
    result: overrides.result ?? null,
    extraction: overrides.extraction ?? null,
    ingest: overrides.ingest ?? null,
    bid: overrides.bid ?? null,
    error: overrides.error ?? null,
    error_type: overrides.error_type ?? null,
  }
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

/** 只刷微任务（首屏 effect 的异步 setState）。 */
async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
  })
}

describe('T-031 App 活跃任务状态上移（解析任务链路）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.getHealth).mockResolvedValue(health)
    vi.mocked(api.listDocuments).mockResolvedValue([])
    vi.mocked(api.listBids).mockResolvedValue([])
    vi.mocked(api.uploadParse).mockResolvedValue({
      job_id: 'job-parse-1',
      doc_id: 'doc-1',
      kind: 'parse',
      status: 'pending',
      job_url: '/api/jobs/job-parse-1',
    })
    vi.mocked(api.getJob).mockResolvedValue(
      mkJob({
        job_id: 'job-parse-1',
        kind: 'parse',
        status: 'running',
        progress: ['解析文档', '结构化抽取'],
      }),
    )
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  async function startParseJob(): Promise<void> {
    render(<App />)
    await flushMicrotasks()

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input).toBeTruthy()
    fireEvent.change(input, {
      target: { files: [new File(['pdf-bytes'], 'demo.pdf', { type: 'application/pdf' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始解析' }))
    // 注册后推进一个轮询周期，getJob 返回 running，底部任务栏与任务进度卡出现
    await advance(1600)
    expect(screen.getByText('任务进度')).toBeInTheDocument()
  }

  it('任务运行中切 tab 再切回，任务进度视图自动恢复，不依赖页面本地 state', async () => {
    await startParseJob()
    const taskCard = screen.getByTestId('parse-task-card')
    expect(within(taskCard).getByText('结构化抽取')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '编写标书' }))
    expect(screen.queryByText('任务进度')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /上传解析/ }))
    // 旧实现：页面重挂后 localJobId 丢失，任务进度卡不再出现
    expect(screen.getByText('任务进度')).toBeInTheDocument()
    expect(within(screen.getByTestId('parse-task-card')).getByText('结构化抽取')).toBeInTheDocument()
  })

  it('在其他 tab 点击底部「查看」，切到上传页并展示对应任务', async () => {
    await startParseJob()

    fireEvent.click(screen.getByRole('button', { name: '编写标书' }))
    expect(screen.queryByText('任务进度')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '查看' }))

    expect(screen.getByRole('heading', { name: '上传解析' })).toBeInTheDocument()
    expect(screen.getByText('任务进度')).toBeInTheDocument()
  })

  it('已在目标 tab 点击「查看」，滚动定位任务卡片（明确反馈，不静默）', async () => {
    await startParseJob()
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView')

    fireEvent.click(screen.getByRole('button', { name: '查看' }))

    expect(scrollSpy).toHaveBeenCalled()
    scrollSpy.mockRestore()
  })

  it('T-030：终态后推进超过 8s 任务/结果视图保留，不再自动回退表单（grace 已移除）', async () => {
    vi.mocked(api.getJob).mockResolvedValue(
      mkJob({
        job_id: 'job-parse-1',
        kind: 'parse',
        status: 'succeeded',
        progress: ['解析文档'],
        result: {
          doc_id: 'doc-1',
          doc_type: 'tender_file',
          parser: 'auto',
          pages: 10,
          sections: 3,
          source_name: 'demo.pdf',
        },
      }),
    )
    await startParseJob()
    // T-034：终态任务不再从任务栏消失——终态行留存，同时提供「查看」与显式「关闭」
    expect(screen.getByRole('button', { name: '查看' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument()
    expect(screen.getByText('解析结果')).toBeInTheDocument()

    // T-030 语义保留：推进超过 8s 不再有 grace 自动回退，终态留存、即停轮询
    await advance(8100)

    expect(screen.getByText('任务进度')).toBeInTheDocument()
    expect(screen.getByText('解析结果')).toBeInTheDocument()
    expect(document.querySelector('input[type="file"]')).toBeNull()

    // T-034：显式「关闭」级联清理集合条目与选中态，任务栏移除、页面回到上传表单
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(screen.queryByRole('button', { name: '查看' })).toBeNull()
    expect(screen.queryByRole('button', { name: '关闭' })).toBeNull()
    expect(document.querySelector('input[type="file"]')).not.toBeNull()
  })
})

describe('T-031 WritePage 受控选择态', () => {
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

  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.getProfileSchema).mockRejectedValue(new Error('no schema'))
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  function renderWritePage(activeJob: JobView | null) {
    return render(
      <WritePage
        initialDocId={null}
        onRegisterJob={vi.fn()}
        onCloseJob={vi.fn()}
        onDeselect={vi.fn()}
        onWritten={vi.fn()}
        activeJob={activeJob}
        selectedJobId={activeJob ? activeJob.job_id : 'job-bid-pending'}
        focusNonce={0}
      />,
    )
  }

  it('受控 props 下任务视图在组件卸载重挂后恢复（旧本地 jobId 方案会丢）', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([doc])
    const job = mkJob({ job_id: 'job-bid-1', kind: 'bid', status: 'running', progress: ['规划章节'] })

    const first = renderWritePage(job)
    await flushMicrotasks()
    expect(screen.getByText('编写进度')).toBeInTheDocument()

    first.unmount()
    expect(screen.queryByText('编写进度')).toBeNull()

    renderWritePage(job)
    await flushMicrotasks()
    expect(screen.getByText('编写进度')).toBeInTheDocument()
    expect(screen.getByText('规划章节')).toBeInTheDocument()
  })

  it('重挂瞬间 documents 尚未返回，任务视图也不得被「暂无文档」EmptyState 覆盖', async () => {
    vi.mocked(api.listDocuments).mockReturnValue(new Promise(() => {}))
    const job = mkJob({ job_id: 'job-bid-2', kind: 'bid', status: 'running' })

    renderWritePage(job)
    await flushMicrotasks()

    expect(screen.getByText('编写进度')).toBeInTheDocument()
    expect(screen.queryByText('暂无已抽取的文档')).toBeNull()
  })

  it('选择态已置位但首帧 activeJob 未到达 → 展示「提交中」而非表单', async () => {
    vi.mocked(api.listDocuments).mockResolvedValue([doc])

    render(
      <WritePage
        initialDocId={null}
        onRegisterJob={vi.fn()}
        onCloseJob={vi.fn()}
        onDeselect={vi.fn()}
        onWritten={vi.fn()}
        activeJob={null}
        selectedJobId="job-bid-pending"
        focusNonce={0}
      />,
    )
    await flushMicrotasks()

    expect(screen.getByText('提交中')).toBeInTheDocument()
  })
})
