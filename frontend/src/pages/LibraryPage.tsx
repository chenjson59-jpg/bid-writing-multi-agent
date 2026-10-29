import { useEffect, useState } from 'react'
import { deleteDocument, describeError, getMarkdown, listDocuments } from '../api'
import type { DocumentSummary, ExtractionDetail } from '../types'
import { Badge, Banner, Button, Card, EmptyState, Modal, Spinner } from '../ui'
import { ExtractionEditor } from './ExtractionEditor'

export function LibraryPage({
  onWrite,
  onChanged,
}: {
  onWrite: (docId: string) => void
  onChanged?: () => void
}) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [previewDoc, setPreviewDoc] = useState<DocumentSummary | null>(null)
  const [previewText, setPreviewText] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [editDoc, setEditDoc] = useState<DocumentSummary | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      setDocuments(await listDocuments())
    } catch (e) {
      setError(describeError(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function remove(doc: DocumentSummary) {
    if (!window.confirm(`确认删除文档 ${doc.source_name || doc.doc_id}？解析结果与上传原件会一起删除。`)) {
      return
    }
    setBusy(doc.doc_id)
    try {
      await deleteDocument(doc.doc_id)
      await load()
      onChanged?.()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(null)
    }
  }

  async function openPreview(doc: DocumentSummary) {
    setPreviewDoc(doc)
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      setPreviewText(await getMarkdown(doc.doc_id))
    } catch (e) {
      setPreviewError(describeError(e))
    } finally {
      setPreviewLoading(false)
    }
  }

  const filtered = documents.filter(
    (d) =>
      d.source_name.toLowerCase().includes(search.toLowerCase()) ||
      d.doc_id.includes(search),
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-100">文档库</h2>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索文件名或 doc_id"
          className="w-64 rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder-slate-500"
        />
      </div>

      {error && <Banner kind="error">{error}</Banner>}
      {notice && <Banner kind="info">{notice}</Banner>}

      {loading ? (
        <Spinner />
      ) : filtered.length === 0 ? (
        <EmptyState title={search ? '无匹配文档' : '文档库为空'} hint={search ? undefined : '请先在「上传解析」页上传 PDF'} />
      ) : (
        <div className="space-y-2">
          {filtered.map((d) => (
            <Card key={d.doc_id}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-200">
                    {d.source_name || d.doc_id}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {d.doc_id} · {d.doc_type || '未知类型'} · {d.pages} 页 · {d.sections} 章节 · {d.parser || '未知解析器'} · {d.parsed_at ?? ''}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {d.has_extraction ? (
                    <>
                      <Badge tone="success">已抽取</Badge>
                      {d.extraction_reviewed ? (
                        <Badge>已核对</Badge>
                      ) : (
                        <Badge tone="warn">待核对</Badge>
                      )}
                    </>
                  ) : (
                    <Badge tone="warn">未抽取</Badge>
                  )}
                  <Button variant="secondary" onClick={() => openPreview(d)}>
                    预览
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setNotice(null)
                      setEditDoc(d)
                    }}
                    disabled={!d.has_extraction}
                  >
                    抽取结果
                  </Button>
                  <Button onClick={() => onWrite(d.doc_id)} disabled={!d.has_extraction}>
                    去编写
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => remove(d)}
                    disabled={busy === d.doc_id}
                  >
                    删除
                  </Button>
                </div>
              </div>
              {!d.has_extraction && (
                <p className="mt-2 text-xs text-amber-400">该文档尚未抽取，需先完成结构化抽取后才能编写标书。</p>
              )}
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={previewDoc !== null}
        title={previewDoc ? `预览：${previewDoc.source_name || previewDoc.doc_id}` : '预览'}
        onClose={() => setPreviewDoc(null)}
      >
        {previewLoading && <Spinner />}
        {previewError && <Banner kind="error">{previewError}</Banner>}
        {!previewLoading && !previewError && (
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-xs text-slate-300">
            {previewText}
          </pre>
        )}
      </Modal>

      {editDoc && (
        <ExtractionEditor
          doc={editDoc}
          onClose={() => setEditDoc(null)}
          onSaved={(detail: ExtractionDetail) => {
            setNotice(
              `已保存抽取结果：评分因素 ${detail.evaluation_factors.length} 条，格式条目 ${detail.bid_format_items.length} 条`,
            )
            setEditDoc(null)
            load()
            onChanged?.()
          }}
        />
      )}
    </div>
  )
}
