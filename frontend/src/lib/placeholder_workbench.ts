/**
 * T-039 标书级占位符填写工作台的纯派生逻辑（无 React / 无副作用）。
 * 组件只负责渲染与交互，聚合、请求体组装与复用判定集中在这里以便单测。
 */
import type {
  BidderProfile,
  MarkerAgg,
  PlaceholderFillItem,
  ProfileSchemaResponse,
  SectionDetail,
} from '../types'

/**
 * 与后端 src/bid_writer/schema.py:36 同形：
 *   PLACEHOLDER_RE = re.compile(r"【待填写[^】]*】")
 * 前后端占位符识别口径必须一致，禁止另立正则。
 * 带 g 标志：请配合 String.match / 新 RegExp(source) 使用，避免共享 lastIndex。
 */
export const PLACEHOLDER_RE = /【待填写[^】]*】/g

/**
 * 高敏关键词黑名单（T-039 前端启发式，T-040 白名单收敛后以后端白名单为准）：
 * 报价/金额/价格/工期/开竣工日期/合同/银行账号/让利/单价类信息只做实例级填写，
 * 默认不进投标人资料库、不进任何 LLM prompt。命中任一关键词即不展示复用勾选。
 */
export const HIGH_SENSITIVITY_KEYWORDS: readonly string[] = [
  '报价',
  '金额',
  '价格',
  '工期',
  '开工',
  '竣工',
  '日期',
  '合同',
  '账号',
  '银行',
  '让利',
  '单价',
]

/**
 * 低敏资料字段白名单（键对应 ProfileSchemaResponse.fields）：
 * 仅纳入可由标记确定性写入的标量/字符串数组字段。T-040 后资料表单保留 7 个
 * 白名单字段，其中 registered_capital/established_date 不经工作台标记映射写入，
 * 故本列表维持 5 个；financials/contact/similar_projects/personnel 已确定为
 * 实例级字段，不进入资料库复用，任何标记都不会勾选复用。
 */
const LOW_SENSITIVITY_PROFILE_FIELDS: readonly string[] = [
  'company_name',
  'legal_representative',
  'address',
  'qualifications',
  'certifications',
]

/** 去掉标记外层的【待填写：与】，取出人工可读标签；无标签空标记返回空字符串。 */
export function markerLabel(marker: string): string {
  return marker.replace(/^【待填写：?/, '').replace(/】$/, '')
}

/**
 * 按完整标记文本去重、按首次出现顺序聚合。
 * 同一标记在多章节出现时合并 total，并在 sections 中逐章记录 count/locked。
 */
export function aggregatePlaceholders(details: SectionDetail[]): MarkerAgg[] {
  const order: string[] = []
  const map = new Map<string, MarkerAgg>()
  for (const detail of details) {
    const matches = detail.markdown.match(PLACEHOLDER_RE) ?? []
    const countByMarker = new Map<string, number>()
    for (const marker of matches) {
      countByMarker.set(marker, (countByMarker.get(marker) ?? 0) + 1)
    }
    for (const [marker, count] of countByMarker) {
      let agg = map.get(marker)
      if (!agg) {
        agg = { marker, total: 0, sections: [] }
        map.set(marker, agg)
        order.push(marker)
      }
      agg.total += count
      agg.sections.push({
        id: detail.id,
        title: detail.title,
        count,
        locked: detail.locked,
      })
    }
  }
  return order.map((marker) => map.get(marker)!)
}

/** 只收集 trim 后非空的输入；section_ids 一律 null（全文替换，候选章节由服务端判定）。 */
export function buildFillItems(
  aggs: MarkerAgg[],
  values: Record<string, string>,
): PlaceholderFillItem[] {
  const items: PlaceholderFillItem[] = []
  for (const agg of aggs) {
    const value = (values[agg.marker] ?? '').trim()
    if (value) {
      items.push({ marker: agg.marker, value, section_ids: null })
    }
  }
  return items
}

/**
 * 匹配标记标签对应的低敏资料字段键；不满足复用条件时返回 null。
 * 判定顺序：空 schema / 空标签 → 高敏黑名单 → 低敏白名单 → label 双向包含。
 */
export function matchProfileField(
  marker: string,
  schema: ProfileSchemaResponse | null,
): string | null {
  if (!schema || Object.keys(schema.fields).length === 0) return null
  const label = markerLabel(marker)
  if (!label) return null
  if (HIGH_SENSITIVITY_KEYWORDS.some((keyword) => marker.includes(keyword))) {
    return null
  }
  for (const [key, item] of Object.entries(schema.fields)) {
    if (!LOW_SENSITIVITY_PROFILE_FIELDS.includes(key)) continue
    if (label === item.label || label.includes(item.label) || item.label.includes(label)) {
      return key
    }
  }
  return null
}

/** 是否展示「存入资料库复用」勾选：仅低敏且能与 schema 既有字段 label 匹配时为 true。 */
export function canReuseToProfile(
  marker: string,
  schema: ProfileSchemaResponse | null,
): boolean {
  return matchProfileField(marker, schema) !== null
}

/** 把一次人工填写合并进既有资料；资质/证书数组去重追加，白名单外字段原样返回。 */
export function mergeProfileReuseValue(
  data: BidderProfile,
  fieldKey: string,
  value: string,
): BidderProfile {
  const next: BidderProfile = { ...data }
  switch (fieldKey) {
    case 'qualifications':
      next.qualifications = uniqueStrings([...(data.qualifications ?? []), value])
      break
    case 'certifications':
      next.certifications = uniqueStrings([...(data.certifications ?? []), value])
      break
    case 'company_name':
      next.company_name = value
      break
    case 'legal_representative':
      next.legal_representative = value
      break
    case 'address':
      next.address = value
      break
    default:
      // 低敏白名单外的字段本期不支持确定性写入，留待 T-040
      break
  }
  return next
}

function uniqueStrings(items: string[]): string[] {
  return [...new Set(items)]
}

/** 按占位标记切分文本（标记保留在结果数组中），供编辑弹窗高亮渲染。 */
export function splitByPlaceholder(text: string): string[] {
  return text.split(new RegExp(`(${PLACEHOLDER_RE.source})`, 'g'))
}

/** 判断切分片段本身是否为一个完整占位标记。 */
export function isPlaceholderMarker(part: string): boolean {
  return new RegExp(`^(?:${PLACEHOLDER_RE.source})$`).test(part)
}
