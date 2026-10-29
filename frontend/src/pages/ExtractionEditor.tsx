// 抽取结果人工校正（P1-10）：三张表编辑 + 基础字段，保存后标记「已核对」。
import { useEffect, useState } from 'react'
import { describeError, getExtraction, saveExtraction } from '../api'
import type {
  DocumentSummary,
  ExtractionDetail,
  ExtractionFactor,
  ExtractionFormatItem,
  ExtractionQualification,
  ExtractionUpdateBody,
} from '../types'
import { Banner, Button, Field, Modal, Spinner } from '../ui'

const INPUT =
  'w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1.5 text-sm text-slate-100 placeholder-slate-500'

const BASIC_LABELS: [string, string][] = [
  ['project_name', '项目名称'],
  ['tender_no', '招标编号'],
  ['budget_amount', '预算金额'],
  ['bid_deadline', '投标截止时间'],
  ['bid_open_place', '开标地点'],
]

export function ExtractionEditor({
  doc,
  onClose,
  onSaved,
}: {
  doc: DocumentSummary
  onClose: () => void
  onSaved: (detail: ExtractionDetail) => void
}) {
  const [detail, setDetail] = useState<ExtractionDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    getExtraction(doc.doc_id)
      .then((data) => {
        if (alive) setDetail(data)
      })
      .catch((e) => {
        if (alive) setError(describeError(e))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [doc.doc_id])

  function update(patch: Partial<ExtractionDetail>) {
    setDetail((d) => (d ? { ...d, ...patch } : d))
  }

  function patchFactor(index: number, patch: Partial<ExtractionFactor>) {
    if (!detail) return
    update({
      evaluation_factors: detail.evaluation_factors.map((f, i) =>
        i === index ? { ...f, ...patch } : f,
      ),
    })
  }

  function patchFormat(index: number, patch: Partial<ExtractionFormatItem>) {
    if (!detail) return
    update({
      bid_format_items: detail.bid_format_items.map((item, i) =>
        i === index ? { ...item, ...patch } : item,
      ),
    })
  }

  function patchQualification(index: number, patch: Partial<ExtractionQualification>) {
    if (!detail) return
    update({
      qualification_items: detail.qualification_items.map((item, i) =>
        i === index ? { ...item, ...patch } : item,
      ),
    })
  }

  function removeAt(
    key: 'evaluation_factors' | 'bid_format_items' | 'qualification_items',
    index: number,
  ) {
    if (!detail) return
    if (key === 'evaluation_factors') {
      update({ evaluation_factors: detail.evaluation_factors.filter((_, i) => i !== index) })
    } else if (key === 'bid_format_items') {
      update({ bid_format_items: detail.bid_format_items.filter((_, i) => i !== index) })
    } else {
      update({
        qualification_items: detail.qualification_items.filter((_, i) => i !== index),
      })
    }
  }

  async function save() {
    if (!detail) return
    setError(null)
    const factors = detail.evaluation_factors.map((f) => ({
      ...f,
      name: f.name.trim(),
      category: f.category.trim() || '其他',
    }))
    const formats = detail.bid_format_items.map((item) => ({
      ...item,
      title: item.title.trim(),
    }))
    const quals = detail.qualification_items.map((item) => ({
      ...item,
      requirement: item.requirement.trim(),
      category: item.category.trim() || '其他',
    }))
    if (factors.some((f) => !f.name)) {
      setError('评分因素名称不能为空，请删除空行或补全名称')
      return
    }
    if (formats.some((item) => !item.title)) {
      setError('格式条目标题不能为空，请删除空行或补全标题')
      return
    }
    if (quals.some((item) => !item.requirement)) {
      setError('资格条件不能为空，请删除空行或补全内容')
      return
    }
    const body: ExtractionUpdateBody = {
      basic: detail.basic,
      evaluation_factors: factors,
      qualification_items: quals,
      bid_format_items: formats,
    }
    setSaving(true)
    try {
      const saved = await saveExtraction(doc.doc_id, body)
      onSaved(saved)
      onClose()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open
      size="lg"
      title={`抽取结果：${doc.source_name || doc.doc_id}`}
      onClose={onClose}
    >
      {loading ? (
        <Spinner />
      ) : !detail ? (
        <Banner kind="error">{error ?? '抽取结果加载失败'}</Banner>
      ) : (
        <div className="max-h-[70vh] space-y-5 overflow-auto pr-1">
          <Banner kind={detail.reviewed_at ? 'info' : 'warn'}>
            {detail.reviewed_at
              ? `已人工核对：${detail.reviewed_at}。修改后保存会重新标记核对时间。`
              : '尚未人工核对：写作前请确认评分因素与格式条目，避免错误抽取直接影响覆盖统计。'}
          </Banner>
          {error && <Banner kind="error">{error}</Banner>}

          <div>
            <p className="mb-2 text-sm font-semibold text-slate-200">基础字段</p>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {BASIC_LABELS.map(([key, label]) => (
                <Field key={key} label={label}>
                  <input
                    className={INPUT}
                    value={detail.basic[key] ?? ''}
                    onChange={(e) =>
                      update({ basic: { ...detail.basic, [key]: e.target.value } })
                    }
                  />
                </Field>
              ))}
            </div>
          </div>

          <TableHeader
            title="评分因素"
            count={detail.evaluation_factors.length}
            onAdd={() =>
              update({
                evaluation_factors: [
                  ...detail.evaluation_factors,
                  { category: '技术', name: '', score: null, source_quote: '' },
                ],
              })
            }
          />
          <div className="space-y-2">
            <div className="grid grid-cols-[6rem_1fr_5rem_1fr_2.5rem] gap-2 text-xs text-slate-500">
              <span>类别</span>
              <span>名称（参与覆盖统计）</span>
              <span>分值</span>
              <span>原文依据</span>
              <span />
            </div>
            {detail.evaluation_factors.map((factor, index) => (
              <div key={index} className="grid grid-cols-[6rem_1fr_5rem_1fr_2.5rem] gap-2">
                <input
                  className={INPUT}
                  value={factor.category}
                  onChange={(e) => patchFactor(index, { category: e.target.value })}
                  placeholder="技术/商务/报价"
                />
                <input
                  className={INPUT}
                  value={factor.name}
                  onChange={(e) => patchFactor(index, { name: e.target.value })}
                />
                <input
                  className={INPUT}
                  type="number"
                  min={0}
                  value={factor.score ?? ''}
                  onChange={(e) =>
                    patchFactor(index, {
                      score: e.target.value === '' ? null : Number(e.target.value),
                    })
                  }
                />
                <input
                  className={INPUT}
                  value={factor.source_quote}
                  onChange={(e) => patchFactor(index, { source_quote: e.target.value })}
                />
                <Button
                  variant="ghost"
                  onClick={() => removeAt('evaluation_factors', index)}
                >
                  删
                </Button>
              </div>
            ))}
            {detail.evaluation_factors.length === 0 && (
              <p className="text-xs text-slate-500">暂无评分因素，可点「新增」补充。</p>
            )}
          </div>

          <TableHeader
            title="格式条目"
            count={detail.bid_format_items.length}
            onAdd={() =>
              update({
                bid_format_items: [...detail.bid_format_items, { title: '', content: '' }],
              })
            }
          />
          <div className="space-y-2">
            <div className="grid grid-cols-[1fr_1.6fr_2.5rem] gap-2 text-xs text-slate-500">
              <span>标题（决定生成哪些章节）</span>
              <span>要求说明</span>
              <span />
            </div>
            {detail.bid_format_items.map((item, index) => (
              <div key={index} className="grid grid-cols-[1fr_1.6fr_2.5rem] gap-2">
                <input
                  className={INPUT}
                  value={item.title}
                  onChange={(e) => patchFormat(index, { title: e.target.value })}
                />
                <input
                  className={INPUT}
                  value={item.content}
                  onChange={(e) => patchFormat(index, { content: e.target.value })}
                />
                <Button variant="ghost" onClick={() => removeAt('bid_format_items', index)}>
                  删
                </Button>
              </div>
            ))}
            {detail.bid_format_items.length === 0 && (
              <p className="text-xs text-slate-500">暂无格式条目，可点「新增」补充。</p>
            )}
          </div>

          <TableHeader
            title="资格条件"
            count={detail.qualification_items.length}
            onAdd={() =>
              update({
                qualification_items: [
                  ...detail.qualification_items,
                  { category: '资质', requirement: '', source_quote: '', thresholds: [] },
                ],
              })
            }
          />
          <div className="space-y-2">
            <div className="grid grid-cols-[6rem_1.4fr_1fr_1.2fr_2.5rem] gap-2 text-xs text-slate-500">
              <span>类别</span>
              <span>要求</span>
              <span>原文依据</span>
              <span>门槛值（、分隔）</span>
              <span />
            </div>
            {detail.qualification_items.map((item, index) => (
              <div
                key={index}
                className="grid grid-cols-[6rem_1.4fr_1fr_1.2fr_2.5rem] gap-2"
              >
                <input
                  className={INPUT}
                  value={item.category}
                  onChange={(e) => patchQualification(index, { category: e.target.value })}
                  placeholder="资质/财务/业绩"
                />
                <input
                  className={INPUT}
                  value={item.requirement}
                  onChange={(e) => patchQualification(index, { requirement: e.target.value })}
                />
                <input
                  className={INPUT}
                  value={item.source_quote}
                  onChange={(e) =>
                    patchQualification(index, { source_quote: e.target.value })
                  }
                />
                <input
                  className={INPUT}
                  value={(item.thresholds ?? []).join('、')}
                  onChange={(e) =>
                    patchQualification(index, {
                      thresholds: e.target.value
                        .split(/[、,，]/)
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                />
                <Button
                  variant="ghost"
                  onClick={() => removeAt('qualification_items', index)}
                >
                  删
                </Button>
              </div>
            ))}
            {detail.qualification_items.length === 0 && (
              <p className="text-xs text-slate-500">暂无资格条件，可点「新增」补充。</p>
            )}
          </div>

          <div className="flex justify-end gap-2 border-t border-slate-800 pt-4">
            <Button variant="secondary" onClick={onClose}>
              取消
            </Button>
            <Button onClick={save} disabled={saving}>
              {saving ? '保存中…' : '保存并标记已核对'}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}

function TableHeader({
  title,
  count,
  onAdd,
}: {
  title: string
  count: number
  onAdd: () => void
}) {
  return (
    <div className="flex items-center justify-between">
      <p className="text-sm font-semibold text-slate-200">
        {title}
        <span className="ml-2 text-xs font-normal text-slate-500">共 {count} 条</span>
      </p>
      <Button variant="secondary" onClick={onAdd}>
        新增
      </Button>
    </div>
  )
}
