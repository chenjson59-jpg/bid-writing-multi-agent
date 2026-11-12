/**
 * T-039 占位符填写工作台纯函数测试（无 React / 无网络）。
 * 口径必须与后端 src/bid_writer/schema.py:36 的 PLACEHOLDER_RE 一致。
 */
import { describe, expect, it } from 'vitest'

import type { ProfileSchemaItem, ProfileSchemaResponse, SectionDetail } from '../src/types'
import {
  HIGH_SENSITIVITY_KEYWORDS,
  PLACEHOLDER_RE,
  aggregatePlaceholders,
  buildFillItems,
  canReuseToProfile,
  isPlaceholderMarker,
  markerLabel,
  matchProfileField,
  mergeProfileReuseValue,
  splitByPlaceholder,
} from '../src/lib/placeholder_workbench'

function mkDetail(
  id: string,
  markdown: string,
  locked = false,
  title?: string,
): SectionDetail {
  return {
    id,
    title: title ?? `章节${id}`,
    order: 0,
    strategy: 'generate',
    locked,
    word_count: markdown.length,
    placeholder_count: 0,
    score_before: 0,
    score_after: 0,
    revisions: 0,
    markdown,
  }
}

function mkSchema(fields: Record<string, string>): ProfileSchemaResponse {
  const entries = Object.entries(fields).map(
    ([key, label]) =>
      [key, { level: 'optional', label, reason: '', matched: false }] satisfies [
        string,
        ProfileSchemaItem,
      ],
  )
  return {
    doc_id: 'doc-1',
    doc_type: 'tender_file',
    inferred_from: [],
    fields: Object.fromEntries(entries),
  }
}

describe('PLACEHOLDER_RE 同源口径', () => {
  it('与后端 schema.py:36 同形，识别带标签与无标签标记', () => {
    expect('【待填写：公司名称】'.match(PLACEHOLDER_RE)).toEqual(['【待填写：公司名称】'])
    expect('前缀【待填写】后缀'.match(PLACEHOLDER_RE)).toEqual(['【待填写】'])
    // 全局正则复用时 lastIndex 不影响 String.match 结果
    expect('a【待填写：x】b【待填写：y】'.match(PLACEHOLDER_RE)).toEqual([
      '【待填写：x】',
      '【待填写：y】',
    ])
  })
})

describe('markerLabel 标记标签提取', () => {
  it('去掉【待填写：前缀与】后缀', () => {
    expect(markerLabel('【待填写：公司名称】')).toBe('公司名称')
  })
  it('无标签空标记返回空字符串', () => {
    expect(markerLabel('【待填写】')).toBe('')
    expect(markerLabel('【待填写：】')).toBe('')
  })
})

describe('aggregatePlaceholders 聚合', () => {
  it('空详情返回空数组', () => {
    expect(aggregatePlaceholders([])).toEqual([])
  })

  it('按完整标记文本去重并统计实例总数，单章同标记多实例只产生一行', () => {
    const aggs = aggregatePlaceholders([
      mkDetail('section_1', '开头【待填写：公司名称】中间【待填写：公司名称】结尾'),
    ])
    expect(aggs).toHaveLength(1)
    expect(aggs[0]).toEqual({
      marker: '【待填写：公司名称】',
      total: 2,
      sections: [
        { id: 'section_1', title: '章节section_1', count: 2, locked: false },
      ],
    })
  })

  it('跨章节聚合：按首次出现顺序排列，sections 记录各章计数与锁定状态', () => {
    const aggs = aggregatePlaceholders([
      mkDetail('section_1', '【待填写：公司名称】【待填写：地址】'),
      mkDetail('section_2', '【待填写：地址】【待填写：公司名称】【待填写：公司名称】', true, '锁定章'),
    ])
    expect(aggs.map((a) => a.marker)).toEqual([
      '【待填写：公司名称】',
      '【待填写：地址】',
    ])
    expect(aggs[0].total).toBe(3)
    expect(aggs[0].sections).toEqual([
      { id: 'section_1', title: '章节section_1', count: 1, locked: false },
      { id: 'section_2', title: '锁定章', count: 2, locked: true },
    ])
    expect(aggs[1].total).toBe(2)
    expect(aggs[1].sections.map((s) => s.id)).toEqual(['section_1', 'section_2'])
  })

  it('无占位符的章节不产生聚合项', () => {
    const aggs = aggregatePlaceholders([
      mkDetail('section_1', '纯正文，无标记'),
      mkDetail('section_2', '【待填写：电话】'),
    ])
    expect(aggs.map((a) => a.marker)).toEqual(['【待填写：电话】'])
    expect(aggs[0].sections).toHaveLength(1)
  })
})

describe('buildFillItems 请求体组装', () => {
  const aggs = aggregatePlaceholders([
    mkDetail('section_1', '【待填写：公司名称】【待填写：地址】【待填写：地址】'),
  ])

  it('只收集 trim 后非空输入，section_ids 一律 null（全文替换）', () => {
    const items = buildFillItems(aggs, {
      '【待填写：公司名称】': '  演示公司  ',
      '【待填写：地址】': '   ',
    })
    expect(items).toEqual([
      { marker: '【待填写：公司名称】', value: '演示公司', section_ids: null },
    ])
  })

  it('全部为空时返回空数组', () => {
    expect(buildFillItems(aggs, {})).toEqual([])
    expect(
      buildFillItems(aggs, {
        '【待填写：公司名称】': '',
        '【待填写：地址】': '  ',
      }),
    ).toEqual([])
  })

  it('输出顺序与聚合顺序一致', () => {
    const items = buildFillItems(aggs, {
      '【待填写：地址】': '某市某路',
      '【待填写：公司名称】': '演示公司',
    })
    expect(items.map((i) => i.marker)).toEqual([
      '【待填写：公司名称】',
      '【待填写：地址】',
    ])
  })
})

describe('canReuseToProfile 资料库复用判定', () => {
  const schema = mkSchema({
    company_name: '公司名称',
    address: '注册地址',
    established_date: '成立日期',
    financials: '财务数据',
    contact: '联系方式',
  })

  it('schema 为空或 fields 为空时 false', () => {
    expect(canReuseToProfile('【待填写：公司名称】', null)).toBe(false)
    expect(canReuseToProfile('【待填写：公司名称】', mkSchema({}))).toBe(false)
  })

  it('标记标签与低敏字段 label 双向包含匹配时 true', () => {
    expect(canReuseToProfile('【待填写：公司名称】', schema)).toBe(true)
    expect(matchProfileField('【待填写：公司名称全称】', schema)).toBe('company_name')
    expect(canReuseToProfile('【待填写：公司注册地址】', schema)).toBe(true)
  })

  it('高敏关键词黑名单命中一律 false，即使 label 能匹配', () => {
    for (const keyword of HIGH_SENSITIVITY_KEYWORDS) {
      const marker = `【待填写：${keyword}说明】`
      expect(canReuseToProfile(marker, schema)).toBe(false)
    }
    // 成立日期：既能与 established_date 的 label 双向包含，又命中「日期」黑名单
    expect(canReuseToProfile('【待填写：公司成立日期】', schema)).toBe(false)
    // 报价类：financials 本身也不在低敏白名单
    expect(canReuseToProfile('【待填写：投标报价】', schema)).toBe(false)
  })

  it('低敏白名单之外的字段（财务/联系方式）不显示复用', () => {
    expect(matchProfileField('【待填写：联系方式】', schema)).toBeNull()
    expect(canReuseToProfile('【待填写：联系方式】', schema)).toBe(false)
    expect(canReuseToProfile('【待填写：财务数据】', schema)).toBe(false)
  })

  it('label 不匹配任何字段时 false', () => {
    expect(canReuseToProfile('【待填写：完全不相干的内容】', schema)).toBe(false)
  })

  it('空标记标签不匹配', () => {
    expect(canReuseToProfile('【待填写】', schema)).toBe(false)
  })
})

describe('mergeProfileReuseValue 资料合并', () => {
  it('标量字段直接覆盖且保留既有其他字段', () => {
    const merged = mergeProfileReuseValue(
      { company_name: '旧公司', address: '旧地址' },
      'company_name',
      '新公司',
    )
    expect(merged).toEqual({ company_name: '新公司', address: '旧地址' })
  })

  it('资质/证书数组去重合并', () => {
    expect(
      mergeProfileReuseValue({ qualifications: ['甲级'] }, 'qualifications', '甲级'),
    ).toEqual({ qualifications: ['甲级'] })
    expect(
      mergeProfileReuseValue({ qualifications: ['甲级'] }, 'qualifications', '乙级'),
    ).toEqual({ qualifications: ['甲级', '乙级'] })
    expect(mergeProfileReuseValue({}, 'certifications', 'ISO9001')).toEqual({
      certifications: ['ISO9001'],
    })
  })

  it('白名单外字段原样返回', () => {
    expect(mergeProfileReuseValue({ company_name: 'X' }, 'financials', '{}')).toEqual({
      company_name: 'X',
    })
  })
})

describe('splitByPlaceholder / isPlaceholderMarker 弹窗高亮渲染辅助', () => {
  it('按标记切分且标记保留在结果数组中', () => {
    expect(splitByPlaceholder('前文【待填写：公司名称】后文')).toEqual([
      '前文',
      '【待填写：公司名称】',
      '后文',
    ])
  })
  it('isPlaceholderMarker 只认完整标记', () => {
    expect(isPlaceholderMarker('【待填写：x】')).toBe(true)
    expect(isPlaceholderMarker('普通文本')).toBe(false)
  })
})
