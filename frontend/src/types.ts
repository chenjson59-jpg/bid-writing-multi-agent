// 与后端 `开发文档.md` §7 接口规范对齐的前端类型定义。

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed'

export interface ParseAccepted {
  job_id: string
  doc_id: string
  kind: string
  status: string
  job_url: string
}

export interface ParseResult {
  doc_id: string
  doc_type: string
  parser: string
  pages: number
  sections: number
  source_name: string
}

export interface SectionResult {
  id: string
  title: string
  order: number
  strategy: string
  score_before: number
  score_after: number
  revisions: number
  word_count: number
  placeholder_count: number
  retrieved_count: number
  scoring_factors: string[]
}

export interface LlmUsage {
  models: string[]
  calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  duration_ms: number
  price_input_per_1k: number
  price_output_per_1k: number
  estimated_cost: number
  cost_estimated: boolean
}

export interface BidSummary {
  doc_id: string
  project_name: string
  bidder: string
  total_sections: number
  total_words: number
  placeholder_count: number
  format_items_total: number
  format_items_covered: number
  evaluation_factors_total: number
  evaluation_factors_covered: number
  uncovered_factors: string[]
  missing_profile_fields: string[]
  sections: SectionResult[]
  warnings: string[]
  llm_usage?: LlmUsage
  supplement_of?: string
  output_dir: string
  markdown_path: string
  docx_path: string
  report_path: string
}

export interface ExtractionSummary {
  project_name?: string
  evaluation_factors?: number
  bid_format_items?: number
  qualifications?: number
}

export interface IngestSummary {
  collection?: string
  added?: number
  updated?: number
  deleted?: number
  unchanged?: number
  total?: number
}

export interface JobView {
  job_id: string
  kind: string
  status: JobStatus
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  options: Record<string, unknown>
  source_name: string | null
  progress: string[]
  result: ParseResult | null
  extraction: ExtractionSummary | null
  ingest: IngestSummary | null
  bid: BidSummary | null
  error: string | null
  error_type: string | null
}

export interface DocumentSummary {
  doc_id: string
  source_name: string
  doc_type: string
  parser: string
  pages: number
  sections: number
  has_extraction: boolean
  extraction_reviewed: boolean
  parsed_at: string | null
}

// 抽取结果人工校正（P1-10）
export interface ExtractionFactor {
  category: string
  name: string
  score: number | null
  source_quote: string
}

export interface ExtractionFormatItem {
  title: string
  content: string
}

export interface ExtractionQualification {
  category: string
  requirement: string
  source_quote: string
  thresholds: string[]
}

export interface ExtractionDetail {
  doc_id: string
  doc_type: string
  basic: Record<string, string>
  evaluation_factors: ExtractionFactor[]
  qualification_items: ExtractionQualification[]
  bid_format_items: ExtractionFormatItem[]
  extraction_meta: Record<string, unknown>
  reviewed_at: string | null
}

export interface ExtractionUpdateBody {
  basic: Record<string, string>
  evaluation_factors: ExtractionFactor[]
  qualification_items: ExtractionQualification[]
  bid_format_items: ExtractionFormatItem[]
}

export interface BidHistoryItem {
  bid_id: string
  generated_at: string | null
  summary: BidSummary
}

// 章节级编辑（P1-2）
export interface SectionInfo {
  id: string
  title: string
  order: number
  strategy: string
  locked: boolean
  word_count: number
  placeholder_count: number
  score_before: number
  score_after: number
  revisions: number
}

export interface SectionDetail extends SectionInfo {
  markdown: string
}

export interface TaskHistoryItem {
  job_id: string
  kind: 'parse' | 'bid' | 'supplement' | string
  doc_id: string | null
  source_name: string | null
  status: JobStatus
  options: Record<string, unknown>
  summary: Record<string, unknown> | null
  error: string | null
  created_at: string | null
  finished_at: string | null
}

export interface BidderProfile {
  company_name?: string
  legal_representative?: string
  registered_capital?: string
  established_date?: string
  address?: string
  qualifications?: string[]
  certifications?: string[]
  similar_projects?: Record<string, unknown>[]
  personnel?: Record<string, unknown>[]
  financials?: Record<string, unknown> | null
  contact?: Record<string, unknown> | null
  notes?: string[]
}

export interface BidCreateBody {
  doc_id: string
  profile?: BidderProfile
  max_revisions?: number | null
  min_score?: number | null
  max_sections?: number | null
  export_word?: boolean
}

export interface HealthInfo {
  status: string
  version: string
  task_backend: string
  broker_ok: boolean | null
  worker_ok: boolean | null
  queue_depth: number | null
}

export interface BidderProfileItem {
  id: number
  company_name: string
  data: BidderProfile
  created_at: string | null
}

export interface BidderProfileUpsert {
  company_name: string
  data: BidderProfile
}
