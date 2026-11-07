/**
 * T-017 标书可信度信号派生逻辑测试。
 * 纯函数、无 React 依赖，沿用 task_state.test.ts 的 vitest 风格。
 */
import { describe, expect, it } from 'vitest'

import type { BidSummary } from '../src/types'
import {
  coverageLayers,
  isRetrievalDegraded,
  ruleErrorSections,
  ungroundedFields,
} from '../src/lib/bid_quality'

function mkSummary(overrides: Partial<BidSummary> = {}): BidSummary {
  return {
    doc_id: '0123456789abcdef',
    project_name: '测试项目',
    bidder: '测试公司',
    total_sections: 1,
    total_words: 100,
    placeholder_count: 0,
    format_items_total: 1,
    format_items_covered: 1,
    evaluation_factors_total: 3,
    evaluation_factors_covered: 3,
    uncovered_factors: [],
    missing_profile_fields: [],
    sections: [],
    warnings: [],
    output_dir: 'upload/bids/x',
    markdown_path: '',
    docx_path: '',
    report_path: '',
    ...overrides,
  }
}

describe('coverageLayers 三层覆盖漏斗', () => {
  it('按计划/正文/证据顺序输出计数', () => {
    const layers = coverageLayers(
      mkSummary({
        evaluation_factors_total: 10,
        evaluation_factors_covered: 10,
        body_factors_covered: 8,
        evidence_factors_covered: 5,
      }),
    )
    expect(layers.map((l) => l.key)).toEqual(['plan', 'body', 'evidence'])
    expect(layers.map((l) => l.covered)).toEqual([10, 8, 5])
    expect(layers.every((l) => l.total === 10)).toBe(true)
  })

  it('旧产物缺省 body/evidence 计数时按 0 处理，不报错', () => {
    const layers = coverageLayers(mkSummary())
    expect(layers[1].covered).toBe(0)
    expect(layers[2].covered).toBe(0)
  })

  it('没有评分因素时三层都是 0/0', () => {
    const layers = coverageLayers(
      mkSummary({ evaluation_factors_total: 0, evaluation_factors_covered: 0 }),
    )
    expect(layers.every((l) => l.covered === 0 && l.total === 0)).toBe(true)
  })
})

describe('isRetrievalDegraded 检索降级判定', () => {
  it('显式 true 才判定降级', () => {
    expect(isRetrievalDegraded(mkSummary({ retrieval_degraded: true }))).toBe(true)
  })

  it('false 与旧产物缺省均判定未降级', () => {
    expect(isRetrievalDegraded(mkSummary({ retrieval_degraded: false }))).toBe(false)
    expect(isRetrievalDegraded(mkSummary())).toBe(false)
  })
})

describe('ruleErrorSections 规则错误章节筛选', () => {
  it('只保留 rule_check 未通过的章节并携带错误信息', () => {
    const summary = mkSummary({
      sections: [
        {
          id: 's1', title: '合格章节', order: 1, strategy: 'generate',
          score_before: 80, score_after: 85, revisions: 0, word_count: 100,
          placeholder_count: 0, retrieved_count: 2, scoring_factors: [],
          rule_check: {
            section_id: 's1', passed: true, citation_markers: [], errors: [], placeholders: [],
          },
        },
        {
          id: 's2', title: '问题章节', order: 2, strategy: 'generate',
          score_before: 60, score_after: 65, revisions: 1, word_count: 100,
          placeholder_count: 1, retrieved_count: 0, scoring_factors: [],
          rule_check: {
            section_id: 's2', passed: false, citation_markers: ['[1]'],
            errors: [
              { code: 'CITE_EMPTY', message: '无检索片段却输出引用 [1]' },
              { code: 'QUOTE_FORBIDDEN', message: '正文出现报价' },
            ],
            placeholders: ['【待填写：金额】'],
          },
        },
        {
          id: 's3', title: '旧产物章节', order: 3, strategy: 'generate',
          score_before: 70, score_after: 70, revisions: 0, word_count: 100,
          placeholder_count: 0, retrieved_count: 0, scoring_factors: [],
        },
      ],
    })
    const issues = ruleErrorSections(summary)
    expect(issues).toHaveLength(1)
    expect(issues[0].id).toBe('s2')
    expect(issues[0].errors.map((e) => e.code)).toEqual(['CITE_EMPTY', 'QUOTE_FORBIDDEN'])
    expect(issues[0].placeholders).toEqual(['【待填写：金额】'])
  })
})

describe('ungroundedFields 未溯源字段提取', () => {
  it('正常返回字符串数组', () => {
    expect(
      ungroundedFields({ ungrounded_fields: ['预算金额', '投标截止时间'] }),
    ).toEqual(['预算金额', '投标截止时间'])
  })

  it('过滤非字符串与空白项', () => {
    expect(
      ungroundedFields({ ungrounded_fields: ['预算金额', 123, null, '  '] }),
    ).toEqual(['预算金额'])
  })

  it('非数组、缺省与 null 输入返回空数组', () => {
    expect(ungroundedFields({ ungrounded_fields: '预算金额' })).toEqual([])
    expect(ungroundedFields({})).toEqual([])
    expect(ungroundedFields(undefined)).toEqual([])
    expect(ungroundedFields(null)).toEqual([])
  })
})
