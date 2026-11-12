/**
 * T-040 WritePage 投标人资料表单瘦身测试（严格 TDD，本文件先于实现提交，先红后绿）。
 *
 * 背景：similar_projects/personnel/financials/contact 四个高敏/单标书字段从资料表单
 * 移出（填写入口改由 T-039 占位符工作台承接），表单只保留 7 个白名单字段：
 * company_name/legal_representative/registered_capital/established_date/address/
 * qualifications/certifications。
 *
 * 约定（与 app_active_job.test.tsx 一致）：
 * - vi.mock 接管整个 api 模块，全部夹具离线返回，禁止真实网络；
 * - vi.useFakeTimers + advanceTimersByTimeAsync 驱动异步，交互一律 fireEvent
 *   （user-event v14 与假定时器死等）；
 * - 每条用例都有业务断言，不写无断言渲染测试。
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
import type { BidderProfileItem, ProfileSchemaResponse } from '../src/types'

const WHITELIST_KEYS = [
  'company_name',
  'legal_representative',
  'registered_capital',
  'established_date',
  'address',
  'qualifications',
  'certifications',
] as const

const REMOVED_KEYS = ['similar_projects', 'personnel', 'financials', 'contact'] as const

const REMOVED_BLOCK_IDS = [
  'profile-similar_projects',
  'profile-personnel',
  'profile-financials',
  'profile-contact',
] as const

const REMOVED_TITLES = ['类似项目业绩', '项目管理人员', '财务数据', '联系方式'] as const

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

function schemaResp(keys: readonly string[]): ProfileSchemaResponse {
  return {
    doc_id: 'doc-1',
    doc_type: 'tender_file',
    fields: Object.fromEntries(
      keys.map((key) => [
        key,
        { level: 'required' as const, label: key, reason: 'fixture', matched: false },
      ]),
    ),
    inferred_from: [],
  }
}

const ALL_11_KEYS = [...WHITELIST_KEYS, ...REMOVED_KEYS]

function renderWritePage() {
  return render(
    <WritePage
      initialDocId={null}
      onRegisterJob={vi.fn()}
      onCloseJob={vi.fn()}
      onDeselect={vi.fn()}
      onWritten={vi.fn()}
      activeJob={null}
      selectedJobId={null}
      focusNonce={0}
    />,
  )
}

/** 假定时器下推进事件循环，刷掉 effect 中的 await/Promise 微任务。 */
async function flush(rounds = 3): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
  }
}

/** 等到首屏异步 effect 落地、资料表单输入框出现。 */
async function waitForForm(): Promise<void> {
  for (let i = 0; i < 10 && !document.getElementById('profile-company_name'); i++) {
    await flush(1)
  }
  expect(document.getElementById('profile-company_name')).not.toBeNull()
}

function setField(id: string, value: string): void {
  const el = document.getElementById(id)
  if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
    throw new Error(`缺少表单字段：${id}`)
  }
  fireEvent.change(el, { target: { value } })
}

/** 按 h3 标题子串找区块标题（区块内还有「已填 N 条」等并列文本，不能用精确匹配）。 */
function findBlockHeading(title: string): Element | null {
  return screen.queryByText(
    (_content, element) =>
      element?.tagName === 'H3' && (element.textContent ?? '').includes(title),
  )
}

describe('T-040 WritePage 资料表单瘦身（7 字段白名单）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(api.listDocuments).mockResolvedValue([doc])
    vi.mocked(api.listProfiles).mockResolvedValue([])
    vi.mocked(api.getProfileSchema).mockRejectedValue(new Error('no schema'))
    vi.mocked(api.createProfile).mockResolvedValue({
      id: 11,
      company_name: '白名单测试公司',
      data: {},
      created_at: null,
    })
    vi.mocked(api.updateProfile).mockResolvedValue({
      id: 11,
      company_name: '白名单测试公司',
      data: {},
      created_at: null,
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('schema 仍返回全部 11 个旧键时，也不渲染四个移出字段区块', async () => {
    vi.mocked(api.getProfileSchema).mockResolvedValue(schemaResp(ALL_11_KEYS))
    renderWritePage()
    await waitForForm()

    for (const title of REMOVED_TITLES) {
      expect(findBlockHeading(title)).toBeNull()
    }
    for (const id of REMOVED_BLOCK_IDS) {
      expect(document.getElementById(id)).toBeNull()
    }
  })

  it('后端 T-040 后仅返回 7 键 schema 时，不渲染移出区块且 7 个白名单输入框存在', async () => {
    vi.mocked(api.getProfileSchema).mockResolvedValue(schemaResp(WHITELIST_KEYS))
    renderWritePage()
    await waitForForm()

    for (const title of REMOVED_TITLES) {
      expect(findBlockHeading(title)).toBeNull()
    }
    for (const id of REMOVED_BLOCK_IDS) {
      expect(document.getElementById(id)).toBeNull()
    }
    for (const key of WHITELIST_KEYS) {
      expect(document.getElementById(`profile-${key}`)).not.toBeNull()
    }
  })

  it('保存到资料库的 payload 只含 7 白名单字段，不含任何移出键', async () => {
    vi.mocked(api.getProfileSchema).mockResolvedValue(schemaResp(WHITELIST_KEYS))
    renderWritePage()
    await waitForForm()

    setField('profile-company_name', '白名单测试公司')
    setField('profile-legal_representative', '张三')
    setField('profile-registered_capital', '1000 万元人民币')
    setField('profile-established_date', '2010-01-01')
    setField('profile-address', '上海市某区某路 1 号')
    setField('profile-qualifications', '建筑工程施工总承包一级\n市政公用工程二级')
    setField('profile-certifications', 'ISO9001 质量管理体系认证')

    fireEvent.click(screen.getByRole('button', { name: '保存到资料库' }))
    await flush(5)

    expect(api.createProfile).toHaveBeenCalledTimes(1)
    expect(api.updateProfile).not.toHaveBeenCalled()
    const body = vi.mocked(api.createProfile).mock.calls[0][0]
    expect(body.company_name).toBe('白名单测试公司')
    expect(Object.keys(body.data).sort()).toEqual([...WHITELIST_KEYS].sort())
    for (const key of REMOVED_KEYS) {
      expect(body.data).not.toHaveProperty(key)
    }
    expect(body.data.qualifications).toEqual([
      '建筑工程施工总承包一级',
      '市政公用工程二级',
    ])
    expect(body.data.certifications).toEqual(['ISO9001 质量管理体系认证'])
  })

  it('载入含旧字段的历史资料不报错：7 字段正常回显，旧高敏值在页面上无任何录入/回显入口', async () => {
    const oldProfile: BidderProfileItem = {
      id: 7,
      company_name: '旧资料公司',
      created_at: null,
      data: {
        company_name: '旧资料公司',
        legal_representative: '李四',
        registered_capital: '500 万元',
        established_date: '2008-05-05',
        address: '旧地址 8 号',
        qualifications: ['资质甲'],
        certifications: ['证书乙'],
        similar_projects: [{ project_name: '历史项目 A', contract_amount: '9999 万元' }],
        personnel: [{ name: '王五', position: '项目经理' }],
        financials: { report_period: '2024 年度', net_profit: '100 万元' },
        contact: { phone: '13800000000', bank_account: '6222000000000000000' },
        notes: ['历史备注'],
      },
    }
    vi.mocked(api.listProfiles).mockResolvedValue([oldProfile])
    vi.mocked(api.getProfileSchema).mockResolvedValue(schemaResp(WHITELIST_KEYS))
    renderWritePage()

    for (let i = 0; i < 10 && !screen.queryByRole('option', { name: '旧资料公司' }); i++) {
      await flush(1)
    }
    const option = screen.getByRole('option', { name: '旧资料公司' })
    const profileSelect = option.closest('select')
    expect(profileSelect).not.toBeNull()
    fireEvent.change(profileSelect as HTMLSelectElement, { target: { value: '7' } })
    await flush(3)

    expect(screen.getByText('已载入「旧资料公司」的资料')).toBeInTheDocument()
    expect((document.getElementById('profile-company_name') as HTMLInputElement).value).toBe(
      '旧资料公司',
    )
    expect(
      (document.getElementById('profile-legal_representative') as HTMLInputElement).value,
    ).toBe('李四')
    expect(
      (document.getElementById('profile-registered_capital') as HTMLInputElement).value,
    ).toBe('500 万元')
    expect(
      (document.getElementById('profile-established_date') as HTMLInputElement).value,
    ).toBe('2008-05-05')
    expect((document.getElementById('profile-address') as HTMLInputElement).value).toBe(
      '旧地址 8 号',
    )
    expect(
      (document.getElementById('profile-qualifications') as HTMLTextAreaElement).value,
    ).toBe('资质甲')
    expect(
      (document.getElementById('profile-certifications') as HTMLTextAreaElement).value,
    ).toBe('证书乙')

    // 旧 JSON 里的高敏值不得出现在页面任何位置，区块 DOM 也不得存在
    const pageText = document.body.textContent ?? ''
    expect(pageText).not.toContain('6222000000000000000')
    expect(pageText).not.toContain('13800000000')
    expect(pageText).not.toContain('历史项目 A')
    for (const id of REMOVED_BLOCK_IDS) {
      expect(document.getElementById(id)).toBeNull()
    }
  })
})
