/**
 * T-038 主题收口组件测试：移除半成品主题切换入口，产品收口为单一深色主题。
 *
 * 约定（同 T-030/T-031）：
 * - vi.mock 接管整个 api 模块，全部夹具离线返回，禁止真实网络；
 * - vi.useFakeTimers + advanceTimersByTimeAsync 驱动健康检查/轮询，禁止真实 sleep；
 * - 交互统一用 fireEvent 派发真实 DOM 事件（假定时器下 user-event 会死等）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'

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

describe('T-038 单一深色主题收口（App，离线夹具）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    // vitest 的 jsdom 不加载 index.html，这里手动还原其静态 <html class="dark">，
    // 模拟真实首屏根元素；收口后 App 不再读写该 class。
    document.documentElement.classList.add('dark')
    vi.mocked(api.getHealth).mockResolvedValue(health)
    vi.mocked(api.listDocuments).mockResolvedValue([doc])
    vi.mocked(api.listBids).mockResolvedValue([])
    vi.mocked(api.listTasks).mockResolvedValue([])
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.getProfileSchema).mockRejectedValue(new Error('no schema'))
    vi.mocked(api.listSections).mockResolvedValue([])
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('渲染后不存在主题切换入口（无「切换到浅色/深色」按钮）', async () => {
    render(<App />)
    await flushMicrotasks()
    await advance(2000)

    expect(screen.queryByRole('button', { name: /切换到/ })).toBeNull()
  })

  it('渲染与轮询过程中不写入 theme 本地存储键，localStorage 中始终无 theme', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')

    render(<App />)
    await flushMicrotasks()
    // 推进超过健康检查（5s）与任务轮询（1.5s）周期，覆盖挂载后全部 effect/interval
    await advance(6000)

    expect(localStorage.getItem('theme')).toBeNull()
    const themeWrites = setItemSpy.mock.calls.filter((args) => args[0] === 'theme')
    expect(themeWrites).toHaveLength(0)

    setItemSpy.mockRestore()
  })

  it('根元素 dark class 始终保留（单一深色主题，静态来自 index.html）', async () => {
    expect(document.documentElement.classList.contains('dark')).toBe(true)

    render(<App />)
    await flushMicrotasks()
    expect(document.documentElement.classList.contains('dark')).toBe(true)

    await advance(6000)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})