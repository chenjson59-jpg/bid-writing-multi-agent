/**
 * T-039 占位符填写工作台组件测试（全离线 vi.mock、假定时器、fireEvent）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'

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
  fillPlaceholders: vi.fn(),
  bidFileUrl: vi.fn(() => ''),
}))

import * as api from '../src/api'
import type {
  BidSummary,
  PlaceholderFillResponse,
  SectionDetail,
  SectionInfo,
  SectionResult,
} from '../src/types'
import {
  PlaceholderWorkbench,
  type PlaceholderWorkbenchHandle,
} from '../src/pages/PlaceholderWorkbench'
import { ResultView } from '../src/pages/ResultView'

const COMPANY = '【待填写：公司名称】'
const PERIOD = '【待填写：工期】'

function mkSectionInfo(overrides: Partial<SectionInfo> = {}): SectionInfo {
  return {
    id: 'section_1',
    title: '投标函',
    order: 1,
    strategy: 'llm_fill',
    locked: false,
    word_count: 10,
    placeholder_count: 0,
    score_before: 0,
    score_after: 0,
    revisions: 0,
    ...overrides,
  }
}

function mkDetail(id: string, markdown: string, locked = false, title = id): SectionDetail {
  return { ...mkSectionInfo({ id, title, locked }), markdown }
}

function mkSummary(overrides: Partial<BidSummary> = {}): BidSummary {
  return {
    doc_id: 'doc-1',
    project_name: '演示项目',
    bidder: '演示公司',
    total_sections: 1,
    total_words: 100,
    placeholder_count: 0,
    format_items_total: 0,
    format_items_covered: 0,
    evaluation_factors_total: 0,
    evaluation_factors_covered: 0,
    uncovered_factors: [],
    missing_profile_fields: [],
    sections: [],
    warnings: [],
    output_dir: 'upload/bids/bid-1',
    markdown_path: 'bid.md',
    docx_path: 'bid.docx',
    report_path: 'report.json',
    ...overrides,
  }
}

function mkSectionResult(overrides: Partial<SectionResult> = {}): SectionResult {
  return {
    id: 'section_1',
    title: '投标函',
    order: 1,
    strategy: 'llm_fill',
    score_before: 0,
    score_after: 0,
    revisions: 0,
    word_count: 10,
    placeholder_count: 2,
    retrieved_count: 0,
    scoring_factors: [],
    ...overrides,
  }
}

function fillResp(overrides: Partial<PlaceholderFillResponse> = {}): PlaceholderFillResponse {
  return {
    summary: mkSummary({ placeholder_count: 1 }),
    filled: [{ marker: COMPANY, replacements: 2, section_ids: ['section_1'] }],
    skipped_locked: [],
    remaining_placeholder_count: 1,
    ...overrides,
  }
}

async function flush(rounds = 2): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
  }
}

const sections: SectionInfo[] = [
  mkSectionInfo({ id: 'section_1', title: '投标函', placeholder_count: 2 }),
  mkSectionInfo({
    id: 'section_2',
    title: '报价表',
    order: 2,
    strategy: 'placeholder',
    locked: true,
    placeholder_count: 1,
  }),
  mkSectionInfo({ id: 'section_3', title: '施工方案', order: 3, strategy: 'generate', placeholder_count: 0 }),
]

const schemaResp = {
  doc_id: 'doc-1',
  doc_type: 'tender_file',
  inferred_from: [],
  fields: {
    company_name: { level: 'optional', label: '公司名称', reason: '', matched: false },
    address: { level: 'optional', label: '注册地址', reason: '', matched: false },
  },
}

function renderWorkbench(props: Record<string, unknown> = {}) {
  const onSaved = vi.fn()
  const onRefreshSections = vi.fn(async () => {})
  const utils = render(
    <PlaceholderWorkbench
      bidId="bid-1"
      docId="doc-1"
      bidder="演示公司"
      sections={sections}
      onSaved={onSaved}
      onRefreshSections={onRefreshSections}
      {...props}
    />,
  )
  return { onSaved, onRefreshSections, ...utils }
}

async function expandWorkbench(): Promise<void> {
  fireEvent.click(screen.getByRole('button', { name: '占位符填写工作台' }))
  await flush()
}

async function openConfirm(): Promise<void> {
  fireEvent.change(screen.getByLabelText(`填写 ${COMPANY}`), {
    target: { value: '演示公司全称' },
  })
  fireEvent.click(screen.getByRole('button', { name: '保存填写内容' }))
  await flush()
}

describe('T-039 PlaceholderWorkbench 工作台（离线夹具）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.getSection).mockImplementation(async (_bid, sid) => {
      if (sid === 'section_1') return mkDetail('section_1', `前文${COMPANY}中间${COMPANY}结尾`)
      if (sid === 'section_2') return mkDetail('section_2', `工期为${PERIOD}天`, true, '报价表')
      return mkDetail(sid, '无标记正文')
    })
    vi.mocked(api.getProfileSchema).mockResolvedValue(schemaResp)
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.createProfile).mockResolvedValue({
      id: 9,
      company_name: '演示公司',
      data: {},
      created_at: null,
    })
    vi.mocked(api.updateProfile).mockResolvedValue({
      id: 9,
      company_name: '演示公司',
      data: {},
      created_at: null,
    })
    vi.mocked(api.fillPlaceholders).mockResolvedValue(fillResp())
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('默认折叠；展开后仅对 placeholder_count>0 的章节各调一次 getSection，每个去重标记一个输入', async () => {
    renderWorkbench()
    expect(screen.queryByLabelText(`填写 ${COMPANY}`)).toBeNull()

    await expandWorkbench()

    const sectionIds = vi.mocked(api.getSection).mock.calls.map((c) => c[1])
    expect(sectionIds.sort()).toEqual(['section_1', 'section_2'])
    expect(screen.getByText(COMPANY)).toBeInTheDocument()
    expect(screen.getByText(PERIOD)).toBeInTheDocument()
    expect(screen.getAllByRole('textbox')).toHaveLength(2)
    // 实例总数与涉及章节名可见
    expect(screen.getByText(/共 2 处/)).toBeInTheDocument()
    expect(screen.getByText(/投标函/)).toBeInTheDocument()
  })

  it('全部输入为空时保存禁用；填写后启用', async () => {
    renderWorkbench()
    await expandWorkbench()
    const saveBtn = screen.getByRole('button', { name: '保存填写内容' })
    expect(saveBtn).toBeDisabled()

    fireEvent.change(screen.getByLabelText(`填写 ${COMPANY}`), { target: { value: '  ' } })
    expect(saveBtn).toBeDisabled()

    fireEvent.change(screen.getByLabelText(`填写 ${COMPANY}`), { target: { value: '演示公司全称' } })
    expect(saveBtn).toBeEnabled()
  })

  it('确认弹窗：取消不发任何请求', async () => {
    renderWorkbench()
    await expandWorkbench()
    await openConfirm()

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/将填写 1 个标记/)).toBeInTheDocument()
    expect(screen.getByText(/触及 1 个章节/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await flush()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.fillPlaceholders).not.toHaveBeenCalled()
  })

  it('确认弹窗：仅保存不加锁发送 lock=false，保存并加锁发送 lock=true', async () => {
    const first = renderWorkbench()
    await expandWorkbench()
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '仅保存不加锁' }))
    await flush()
    expect(api.fillPlaceholders).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.fillPlaceholders).mock.calls[0]).toEqual([
      'bid-1',
      {
        items: [{ marker: COMPANY, value: '演示公司全称', section_ids: null }],
        lock: false,
      },
    ])
    first.unmount()

    vi.clearAllMocks()
    vi.mocked(api.fillPlaceholders).mockResolvedValue(fillResp())
    renderWorkbench()
    await expandWorkbench()
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '保存并加锁' }))
    await flush()
    expect(api.fillPlaceholders).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.fillPlaceholders).mock.calls[0][1]).toMatchObject({ lock: true })
  })

  it('一次保存只发一次 fill 请求；成功后回调 onSaved/onRefreshSections、清空输入并重新聚合剩余标记', async () => {
    const markdownStore: Record<string, string> = {
      section_1: `前文${COMPANY}中间${COMPANY}结尾`,
      section_2: `工期为${PERIOD}天`,
    }
    vi.mocked(api.getSection).mockImplementation(async (_bid, sid) =>
      mkDetail(sid, markdownStore[sid] ?? '无标记', sid === 'section_2', sid === 'section_2' ? '报价表' : sid),
    )
    const onRefreshSections = vi.fn(async () => {
      markdownStore.section_1 = '该章已无占位符'
    })
    const { onSaved } = renderWorkbench({ onRefreshSections })
    await expandWorkbench()
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '保存并加锁' }))
    await flush(3)

    expect(api.fillPlaceholders).toHaveBeenCalledTimes(1)
    expect(onSaved).toHaveBeenCalledTimes(1)
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ placeholder_count: 1 }))
    expect(onRefreshSections).toHaveBeenCalledTimes(1)
    // 已填输入被清空，公司名称标记消失，剩余工期标记仍在且输入为空
    expect(screen.queryByLabelText(`填写 ${COMPANY}`)).toBeNull()
    const periodInput = screen.getByLabelText(`填写 ${PERIOD}`)
    expect(periodInput).toHaveValue('')
    // 重新聚合：展开一轮 2 次 getSection + 保存后一轮 2 次
    expect(vi.mocked(api.getSection)).toHaveBeenCalledTimes(4)
  })

  it('skipped_locked 非空时展示 info 横幅指明被跳过的标记', async () => {
    vi.mocked(api.fillPlaceholders).mockResolvedValue(
      fillResp({
        filled: [{ marker: COMPANY, replacements: 2, section_ids: ['section_1'] }],
        skipped_locked: [{ marker: PERIOD, section_ids: ['section_2'] }],
      }),
    )
    renderWorkbench()
    await expandWorkbench()
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '仅保存不加锁' }))
    await flush(3)

    const banner = screen.getByText(/因章节锁定被跳过/)
    expect(banner).toBeInTheDocument()
    expect(banner.textContent).toContain(PERIOD)
  })

  it('复用勾选默认不勾：保存时零 profile 调用', async () => {
    renderWorkbench()
    await expandWorkbench()
    const checkbox = screen.getByLabelText(`存入资料库复用：${COMPANY}`)
    expect(checkbox).not.toBeChecked()

    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '仅保存不加锁' }))
    await flush(3)

    expect(api.listProfiles).not.toHaveBeenCalled()
    expect(api.createProfile).not.toHaveBeenCalled()
    expect(api.updateProfile).not.toHaveBeenCalled()
  })

  it('高敏标记行不显示复用勾选', async () => {
    renderWorkbench()
    await expandWorkbench()
    expect(screen.queryByLabelText(`存入资料库复用：${PERIOD}`)).toBeNull()
    expect(screen.getByLabelText(`存入资料库复用：${COMPANY}`)).toBeInTheDocument()
  })

  it('勾选复用且无既有 profile：回填成功后 createProfile，公司名取 bidder', async () => {
    renderWorkbench()
    await expandWorkbench()
    fireEvent.click(screen.getByLabelText(`存入资料库复用：${COMPANY}`))
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '仅保存不加锁' }))
    await flush(3)

    expect(api.listProfiles).toHaveBeenCalledTimes(1)
    expect(api.createProfile).toHaveBeenCalledTimes(1)
    expect(api.createProfile).toHaveBeenCalledWith({
      company_name: '演示公司',
      data: { company_name: '演示公司全称' },
    })
    expect(api.updateProfile).not.toHaveBeenCalled()
  })

  it('勾选复用且存在同公司 profile：走 updateProfile 并保留既有资料', async () => {
    vi.mocked(api.listProfiles).mockResolvedValue([
      { id: 7, company_name: '演示公司', data: { address: '旧地址' }, created_at: null },
    ])
    renderWorkbench()
    await expandWorkbench()
    fireEvent.click(screen.getByLabelText(`存入资料库复用：${COMPANY}`))
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '保存并加锁' }))
    await flush(3)

    expect(api.createProfile).not.toHaveBeenCalled()
    expect(api.updateProfile).toHaveBeenCalledTimes(1)
    expect(api.updateProfile).toHaveBeenCalledWith(7, {
      company_name: '演示公司',
      data: { address: '旧地址', company_name: '演示公司全称' },
    })
  })

  it('资料库保存失败不回滚章节回填：错误横幅可见且 onSaved 已生效', async () => {
    const { onSaved } = renderWorkbench()
    vi.mocked(api.createProfile).mockRejectedValueOnce(new Error('资料库 500'))
    await expandWorkbench()
    fireEvent.click(screen.getByLabelText(`存入资料库复用：${COMPANY}`))
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '仅保存不加锁' }))
    await flush(4)

    expect(api.fillPlaceholders).toHaveBeenCalledTimes(1)
    expect(onSaved).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/资料库保存失败/)).toBeInTheDocument()
  })

  it('回填接口失败：展示错误横幅且不产生任何 profile 请求，弹窗保留', async () => {
    vi.mocked(api.fillPlaceholders).mockRejectedValueOnce(new Error('422 参数校验失败'))
    renderWorkbench()
    await expandWorkbench()
    await openConfirm()
    fireEvent.click(screen.getByRole('button', { name: '保存并加锁' }))
    await flush(3)

    expect(screen.getByText(/422 参数校验失败/)).toBeInTheDocument()
    expect(api.listProfiles).not.toHaveBeenCalled()
    expect(api.createProfile).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '保存并加锁' })).toBeInTheDocument()
  })

  it('focusMarker：折叠态调用后自动展开、聚焦输入并滚动到位', async () => {
    const ref = createRef<PlaceholderWorkbenchHandle>()
    render(
      <PlaceholderWorkbench
        ref={ref}
        bidId="bid-1"
        docId="doc-1"
        bidder="演示公司"
        sections={sections}
        onSaved={vi.fn()}
        onRefreshSections={vi.fn(async () => {})}
      />,
    )
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView')
    expect(screen.queryByLabelText(`填写 ${COMPANY}`)).toBeNull()

    await act(async () => {
      ref.current?.focusMarker(COMPANY)
    })
    await flush(2)

    const input = screen.getByLabelText(`填写 ${COMPANY}`)
    expect(document.activeElement).toBe(input)
    expect(scrollSpy).toHaveBeenCalled()
  })
})

describe('T-039 ResultView 集成（下载警告与标记高亮跳转）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.listSections).mockResolvedValue(sections)
    vi.mocked(api.getSection).mockImplementation(async (_bid, sid) => {
      if (sid === 'section_1') return mkDetail('section_1', `前文${COMPANY}中间${COMPANY}结尾`)
      if (sid === 'section_2') return mkDetail('section_2', `工期为${PERIOD}天`, true, '报价表')
      return mkDetail(sid, '无标记正文')
    })
    vi.mocked(api.getProfileSchema).mockResolvedValue(schemaResp)
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.fillPlaceholders).mockResolvedValue(fillResp())
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('placeholder_count>0 显示含种数的下载警告；=0 不渲染；下载按钮不被拦截', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const summary = mkSummary({ placeholder_count: 3, sections: [mkSectionResult()] })
    render(<ResultView bid={summary} bidId="bid-1" />)
    await flush()

    const warn = screen.getByText(/产物仍含 3 种占位符未填写/)
    expect(warn).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下载 Markdown' }))
    expect(openSpy).toHaveBeenCalledTimes(1)
    openSpy.mockRestore()

    const zero = mkSummary({ placeholder_count: 0 })
    const utils = render(<ResultView bid={zero} bidId="bid-2" />)
    await flush()
    expect(utils.queryByText(/产物仍含/)).toBeNull()
  })

  it('章节编辑弹窗把占位标记渲染为 mark，点击后工作台展开并聚焦对应输入', async () => {
    const summary = mkSummary({ placeholder_count: 2, sections: [mkSectionResult()] })
    render(<ResultView bid={summary} bidId="bid-1" />)
    await flush()

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    await flush()
    const mark = screen.getByText(COMPANY)
    expect(mark.tagName).toBe('MARK')
    fireEvent.click(mark)
    await flush(3)

    // 编辑弹窗关闭，工作台在同页展开并出现对应输入
    expect(screen.queryByRole('dialog')).toBeNull()
    const input = screen.getByLabelText(`填写 ${COMPANY}`)
    expect(input).toBeInTheDocument()
    expect(document.activeElement).toBe(input)
  })
})
