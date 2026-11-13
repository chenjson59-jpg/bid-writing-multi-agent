/**
 * T-037 「返回表单（后台继续）」按钮可点击辨识度回归测试。
 *
 * 背景：该按钮在 WritePage/UploadPage 的深色任务卡（bg-slate-900）内使用 ghost
 * 变体（透明底、无边框，仅 hover 出现 slate-800），静态下与卡片/全局任务栏
 * 糊成一体，用户无法直观看出可点击。T-037 在两处调用点局部改用 secondary
 * 实底变体，ui.tsx 的 ghost 全局默认样式保持不变（其余 10 处使用点不回归）。
 *
 * 约定（与 app_active_job.test.tsx 一致）：
 * - vi.mock 接管整个 api 模块，全部夹具离线返回，禁止真实网络；
 * - vi.useFakeTimers + Promise 微任务驱动异步，交互一律 fireEvent
 *   （user-event v14 与假定时器死等）；
 * - jsdom 不计算 CSS 渲染颜色，纯视觉变体以 Button 产出的确定性 class 锁定
 *   （secondary 实底标记存在、ghost 透明底标记不存在），并保留点击行为断言；
 *   最终配色目检步骤写入任务卡与工作日志，不做无依据的「已验证」标注。
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
  fillPlaceholders: vi.fn(),
  sectionFileUrl: vi.fn(() => ''),
  listProfiles: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  getProfileSchema: vi.fn(),
  bidFileUrl: vi.fn(() => ''),
}))

import * as api from '../src/api'
import { WritePage } from '../src/pages/WritePage'
import { UploadPage } from '../src/pages/UploadPage'
import type { JobView } from '../src/types'

function mkJob(overrides: Partial<JobView> = {}): JobView {
  return {
    job_id: overrides.job_id ?? 'job-t037',
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

async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
  })
}

/** 锁定「返回表单」按钮为 secondary 实底变体，而非透明无边框的 ghost。 */
function expectSecondaryAffordance(): HTMLElement {
  const btn = screen.getByRole('button', { name: '返回表单（后台继续）' })
  // secondary 深色实底（dark:bg-slate-700），在 slate-900 卡片上具备静态边界
  expect(btn.className).toContain('bg-slate-700')
  // ghost 的透明底标记不得残留
  expect(btn.className).not.toContain('bg-transparent')
  return btn
}

describe('T-037 「返回表单（后台继续）」按钮辨识度（离线夹具）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.listDocuments).mockResolvedValue([doc])
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.getProfileSchema).mockRejectedValue(new Error('no schema'))
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('WritePage 编写进度卡内按钮为实底 secondary 变体，且点击仍触发 onDeselect', async () => {
    const onDeselect = vi.fn()
    const job = mkJob({
      job_id: 'job-bid-t037',
      kind: 'bid',
      status: 'running',
      progress: ['规划章节'],
    })
    render(
      <WritePage
        initialDocId={null}
        onRegisterJob={vi.fn()}
        onCloseJob={vi.fn()}
        onDeselect={onDeselect}
        onWritten={vi.fn()}
        activeJob={job}
        selectedJobId={job.job_id}
        focusNonce={0}
      />,
    )
    await flushMicrotasks()

    expect(screen.getByText('编写进度')).toBeInTheDocument()
    const btn = expectSecondaryAffordance()

    fireEvent.click(btn)
    await flushMicrotasks()
    expect(onDeselect).toHaveBeenCalledTimes(1)
  })

  it('UploadPage 任务进度卡内按钮为实底 secondary 变体，且点击仍触发 onDeselect', async () => {
    const onDeselect = vi.fn()
    const job = mkJob({
      job_id: 'job-parse-t037',
      kind: 'parse',
      status: 'running',
      progress: ['解析文档', '结构化抽取'],
    })
    render(
      <UploadPage
        onRegisterJob={vi.fn()}
        onCloseJob={vi.fn()}
        onDeselect={onDeselect}
        activeJob={job}
        selectedJobId={job.job_id}
        focusNonce={0}
        onUploaded={vi.fn()}
        onWrite={vi.fn()}
      />,
    )
    await flushMicrotasks()

    expect(screen.getByText('任务进度')).toBeInTheDocument()
    const btn = expectSecondaryAffordance()

    fireEvent.click(btn)
    await flushMicrotasks()
    expect(onDeselect).toHaveBeenCalledTimes(1)
  })
})