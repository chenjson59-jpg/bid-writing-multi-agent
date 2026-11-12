import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  createProfile,
  describeError,
  fillPlaceholders,
  getProfileSchema,
  getSection,
  listProfiles,
  updateProfile,
} from '../api'
import {
  aggregatePlaceholders,
  buildFillItems,
  canReuseToProfile,
  matchProfileField,
  mergeProfileReuseValue,
} from '../lib/placeholder_workbench'
import type {
  BidderProfile,
  BidSummary,
  MarkerAgg,
  PlaceholderFillItem,
  ProfileSchemaResponse,
  SectionInfo,
} from '../types'
import { Badge, Banner, Button, Card, Modal, Spinner } from '../ui'

export interface PlaceholderWorkbenchHandle {
  /** 展开工作台并把指定标记的输入框聚焦、滚动到位（章节弹窗点击 mark 时调用）。 */
  focusMarker: (marker: string) => void
}

interface PlaceholderWorkbenchProps {
  bidId: string
  docId: string
  bidder: string
  sections: SectionInfo[]
  onSaved: (summary: BidSummary) => void
  onRefreshSections: () => void | Promise<void>
}

/**
 * T-039 标书级占位符填写工作台：去重标记一输入、全文一次替换。
 * 纯单页内存态，不引入 localStorage / 富文本 / 新依赖；高敏数据默认不入库。
 */
export const PlaceholderWorkbench = forwardRef<
  PlaceholderWorkbenchHandle,
  PlaceholderWorkbenchProps
>(function PlaceholderWorkbench(
  { bidId, docId, bidder, sections, onSaved, onRefreshSections },
  ref,
) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [aggs, setAggs] = useState<MarkerAgg[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [reuse, setReuse] = useState<Record<string, boolean>>({})
  const [schema, setSchema] = useState<ProfileSchemaResponse | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [skippedMarkers, setSkippedMarkers] = useState<string[]>([])
  const [pendingFocus, setPendingFocus] = useState<string | null>(null)

  // 保存回调后父组件会刷新 sections，回调闭包里通过 ref 读取最新章节列表
  const sectionsRef = useRef(sections)
  sectionsRef.current = sections
  const inputRefs = useRef(new Map<string, HTMLInputElement>())
  // 同一 docId 只拉一次 schema；失败降级为 null（不显示复用勾选，不阻断填写）
  const schemaDocRef = useRef<string | null>(null)

  const loadDetails = useCallback(
    async (sectionList: SectionInfo[]) => {
      const targets = sectionList.filter((s) => s.placeholder_count > 0)
      if (targets.length === 0) {
        setAggs([])
        return
      }
      setLoading(true)
      try {
        const details = await Promise.all(targets.map((s) => getSection(bidId, s.id)))
        setAggs(aggregatePlaceholders(details))
        setLoadError(null)
      } catch (e) {
        setLoadError(describeError(e))
      } finally {
        setLoading(false)
      }
    },
    [bidId],
  )

  const loadSchema = useCallback(async () => {
    if (!docId || schemaDocRef.current === docId) return
    schemaDocRef.current = docId
    try {
      setSchema(await getProfileSchema(docId))
    } catch {
      setSchema(null)
    }
  }, [docId])

  const expand = useCallback(async () => {
    setOpen(true)
    await Promise.all([loadDetails(sectionsRef.current), loadSchema()])
  }, [loadDetails, loadSchema])

  useImperativeHandle(
    ref,
    () => ({
      focusMarker: (marker: string) => {
        setOpen(true)
        setPendingFocus(marker)
        void Promise.all([loadDetails(sectionsRef.current), loadSchema()])
      },
    }),
    [loadDetails, loadSchema],
  )

  // 详情加载完成后把待聚焦标记的输入框聚焦并滚动到位；标记已不存在则放弃
  useEffect(() => {
    if (!pendingFocus || loading) return
    const el = inputRefs.current.get(pendingFocus)
    if (el) {
      el.focus()
      el.scrollIntoView({ block: 'center' })
      setPendingFocus(null)
    } else if (!aggs.some((a) => a.marker === pendingFocus)) {
      setPendingFocus(null)
    }
  }, [pendingFocus, aggs, loading])

  const items = useMemo(() => buildFillItems(aggs, values), [aggs, values])
  const activeMarkers = useMemo(() => new Set(items.map((i) => i.marker)), [items])
  const touchedSections = useMemo(() => {
    const ids = new Set<string>()
    for (const agg of aggs) {
      if (!activeMarkers.has(agg.marker)) continue
      for (const s of agg.sections) ids.add(s.id)
    }
    const byId = new Map(sections.map((s) => [s.id, s]))
    return [...ids].map((id) => byId.get(id)).filter((s): s is SectionInfo => !!s)
  }, [aggs, activeMarkers, sections])
  const lockedTouched = touchedSections.filter((s) => s.locked)

  async function toggleOpen() {
    if (open) {
      setOpen(false)
      return
    }
    await expand()
  }

  /** 回填成功后，把勾选的低敏字段逐个写入资料库；任何失败都不回滚章节回填。 */
  async function saveReuse(markers: string[], submittedItems: PlaceholderFillItem[]) {
    try {
      const profiles = await listProfiles()
      const existing = profiles.find((p) => p.company_name === bidder) ?? null
      let targetId = existing?.id ?? null
      let data: BidderProfile = existing ? { ...existing.data } : {}
      const valueMap = new Map(submittedItems.map((i) => [i.marker, i.value]))
      const failed: string[] = []
      for (const marker of markers) {
        const fieldKey = matchProfileField(marker, schema)
        const value = valueMap.get(marker)
        if (!fieldKey || !value) continue
        data = mergeProfileReuseValue(data, fieldKey, value)
        try {
          if (targetId === null) {
            const saved = await createProfile({
              company_name: bidder || '未命名投标人',
              data,
            })
            targetId = saved.id
          } else {
            await updateProfile(targetId, {
              company_name: existing?.company_name ?? bidder,
              data,
            })
          }
        } catch {
          failed.push(marker)
        }
      }
      if (failed.length > 0) {
        setProfileError(
          `资料库保存失败：${failed.join('、')}（章节回填已生效，不会回滚）`,
        )
      }
    } catch (e) {
      setProfileError(`资料库保存失败：${describeError(e)}（章节回填已生效，不会回滚）`)
    }
  }

  async function submitFill(lock: boolean) {
    if (busy || items.length === 0) return
    setBusy(true)
    setActionError(null)
    setProfileError(null)
    const submittedItems: PlaceholderFillItem[] = items
    const checkedMarkers = submittedItems
      .map((i) => i.marker)
      .filter((marker) => reuse[marker] === true)
    try {
      const resp = await fillPlaceholders(bidId, { items: submittedItems, lock })
      setConfirmOpen(false)
      onSaved(resp.summary)
      await onRefreshSections()
      setSkippedMarkers(resp.skipped_locked.map((x) => x.marker))
      if (checkedMarkers.length > 0) {
        await saveReuse(checkedMarkers, submittedItems)
      }
      // 已填输入与勾选清空，按最新章节正文重新聚合剩余标记
      setValues({})
      setReuse({})
      await loadDetails(sectionsRef.current)
    } catch (e) {
      // 失败保留弹窗与已填输入，便于用户重试或取消
      setActionError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  function setInputRef(el: HTMLInputElement | null, marker: string) {
    if (el) inputRefs.current.set(marker, el)
    else inputRefs.current.delete(marker)
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" onClick={toggleOpen}>
          {open ? '折叠工作台' : '占位符填写工作台'}
        </Button>
        {open && aggs.length > 0 && <Badge tone="warn">剩余 {aggs.length} 种标记</Badge>}
      </div>

      {open && (
        <div className="mt-4 space-y-3">
          <p className="text-xs text-slate-500">
            一个去重标记对应一个输入框，保存时一次替换全部章节中的同名实例；报价、工期、合同、
            银行账号等高敏信息仅写入本标书章节，不进入投标人资料库，也不会进入任何模型提示词。
          </p>
          {loadError && <Banner kind="error">{loadError}</Banner>}
          {profileError && <Banner kind="error">{profileError}</Banner>}
          {skippedMarkers.length > 0 && (
            <Banner kind="info">
              以下标记因章节锁定被跳过：{skippedMarkers.join('、')}
            </Banner>
          )}

          {loading ? (
            <div className="flex items-center gap-2 text-sm text-slate-400">
              <Spinner />
              正在加载章节正文…
            </div>
          ) : aggs.length === 0 ? (
            <p className="text-sm text-slate-400">当前产物没有待填写占位符。</p>
          ) : (
            <div className="space-y-3">
              {aggs.map((agg) => {
                const reusable = canReuseToProfile(agg.marker, schema)
                return (
                  <div
                    key={agg.marker}
                    className="rounded-md border border-slate-800 bg-slate-950/40 p-3"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm text-amber-300">{agg.marker}</span>
                      <Badge tone="muted">共 {agg.total} 处</Badge>
                      <span className="text-xs text-slate-500">
                        涉及章节：
                        {agg.sections
                          .map((s) => (s.locked ? `${s.title}（已锁定）` : s.title))
                          .join('、')}
                      </span>
                    </div>
                    <input
                      ref={(el) => setInputRef(el, agg.marker)}
                      type="text"
                      aria-label={`填写 ${agg.marker}`}
                      className="mt-2 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200"
                      value={values[agg.marker] ?? ''}
                      onChange={(e) =>
                        setValues((prev) => ({ ...prev, [agg.marker]: e.target.value }))
                      }
                      placeholder="输入替换内容（留空则不处理该标记）"
                    />
                    {reusable && (
                      <label className="mt-2 flex items-center gap-2 text-xs text-slate-400">
                        <input
                          type="checkbox"
                          aria-label={`存入资料库复用：${agg.marker}`}
                          checked={reuse[agg.marker] ?? false}
                          onChange={(e) =>
                            setReuse((prev) => ({ ...prev, [agg.marker]: e.target.checked }))
                          }
                        />
                        存入资料库复用（默认不勾；仅低敏字段，高敏信息不入库）
                      </label>
                    )}
                  </div>
                )
              })}
              <div className="flex justify-end">
                <Button
                  onClick={() => {
                    setActionError(null)
                    setConfirmOpen(true)
                  }}
                  disabled={items.length === 0 || busy}
                >
                  {busy ? <Spinner /> : '保存填写内容'}
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      <Modal
        open={confirmOpen}
        title="确认保存占位符填写"
        onClose={() => {
          if (!busy) setConfirmOpen(false)
        }}
      >
        <div className="space-y-3 text-sm text-slate-300">
          <p>
            将填写 {items.length} 个标记、触及 {touchedSections.length} 个章节。
          </p>
          {lockedTouched.length > 0 && (
            <p className="text-amber-300">
              其中已锁定章节：{lockedTouched.map((s) => s.title).join('、')}
              ；锁定章节中的标记会被跳过并在保存后提示。
            </p>
          )}
          <p>
            「保存并加锁」会对实际发生替换的章节加锁，此后批量重写将跳过这些章节；
            「仅保存不加锁」不改变章节锁定状态。
          </p>
          {actionError && <Banner kind="error">{actionError}</Banner>}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="secondary"
              onClick={() => {
                setConfirmOpen(false)
                setActionError(null)
              }}
              disabled={busy}
            >
              取消
            </Button>
            <Button variant="secondary" onClick={() => submitFill(false)} disabled={busy}>
              仅保存不加锁
            </Button>
            <Button variant="primary" onClick={() => submitFill(true)} disabled={busy}>
              {busy ? <Spinner /> : '保存并加锁'}
            </Button>
          </div>
        </div>
      </Modal>
    </Card>
  )
})
