/**
 * T-017 标书可信度信号的纯派生逻辑（无 React / 无副作用）。
 * ResultView 与 ExtractionEditor 只负责渲染，所有判定集中在这里以便单测。
 */
import type { BidSummary, RuleCheckError } from '../types'

export interface CoverageLayer {
  key: 'plan' | 'body' | 'evidence'
  label: string
  covered: number
  total: number
}

/** 计划/正文/证据三层覆盖漏斗；旧产物缺省计数按 0 处理。 */
export function coverageLayers(summary: BidSummary): CoverageLayer[] {
  const total = summary.evaluation_factors_total ?? 0
  return [
    {
      key: 'plan',
      label: '计划覆盖',
      covered: summary.evaluation_factors_covered ?? 0,
      total,
    },
    {
      key: 'body',
      label: '正文覆盖',
      covered: summary.body_factors_covered ?? 0,
      total,
    },
    {
      key: 'evidence',
      label: '证据覆盖',
      covered: summary.evidence_factors_covered ?? 0,
      total,
    },
  ]
}

/** 检索降级（集合缺失 / Embedding 失败 / 查询异常），旧产物缺省视为未降级。 */
export function isRetrievalDegraded(summary: BidSummary): boolean {
  return summary.retrieval_degraded === true
}

export interface SectionRuleIssue {
  id: string
  title: string
  errors: RuleCheckError[]
  placeholders: string[]
}

/** 筛出确定性规则检查未通过的章节；无 rule_check 的旧产物章节不纳入。 */
export function ruleErrorSections(summary: BidSummary): SectionRuleIssue[] {
  return summary.sections
    .filter((s) => s.rule_check != null && !s.rule_check.passed)
    .map((s) => {
      const check = s.rule_check!
      return {
        id: s.id,
        title: s.title,
        errors: check.errors ?? [],
        placeholders: check.placeholders ?? [],
      }
    })
}

/** 从 extraction_meta 安全提取未溯源字段；类型异常或缺省返回空数组。 */
export function ungroundedFields(
  meta: Record<string, unknown> | null | undefined,
): string[] {
  const raw = meta?.ungrounded_fields
  if (!Array.isArray(raw)) return []
  return raw.filter(
    (item): item is string => typeof item === 'string' && item.trim().length > 0,
  )
}
