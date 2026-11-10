/** 全局任务状态纯函数的单元测试。
 *
 * 这些函数没有副作用，没有 React 依赖，直接 vitest 跑，不需要 jsdom。
 * 如果某个函数在 App.tsx / GlobalTaskBar 里改了行为，这里必然失败。
 */
import { describe, expect, it } from 'vitest'

import type { JobView } from '../src/types'
import {
  decidePollOutcome,
  derivePageJobState,
  filterRunning,
  hasRunning,
  isRunning,
  isTerminal,
  jobEquals,
  kindLabel,
  kindTab,
  nextHistoryRefreshNonce,
  pickJobForKind,
  planJobJump,
  shouldEmitJobSuccess,
  statusLabel,
  terminalOutcome,
} from '../src/lib/task_state'

// ---------- 测试用夹具（最小必要 JobView） ----------

function mkJob(overrides: Partial<JobView> = {}): JobView {
  return {
    job_id: overrides.job_id ?? 'job-11111111',
    kind: overrides.kind ?? 'parse',
    status: overrides.status ?? 'pending',
    created_at: overrides.created_at ?? null,
    started_at: overrides.started_at ?? null,
    finished_at: overrides.finished_at ?? null,
    options: overrides.options ?? {},
    source_name: overrides.source_name ?? 'demo.pdf',
    progress: overrides.progress ?? [],
    result: overrides.result ?? null,
    extraction: overrides.extraction ?? null,
    ingest: overrides.ingest ?? null,
    bid: overrides.bid ?? null,
    error: overrides.error ?? null,
    error_type: overrides.error_type ?? null,
  }
}

// ============================================================
// isRunning / isTerminal / terminalOutcome
// ============================================================

describe('isRunning', () => {
  it('null / undefined → false', () => {
    expect(isRunning(null)).toBe(false)
    expect(isRunning(undefined)).toBe(false)
  })

  it('pending / running → true', () => {
    expect(isRunning(mkJob({ status: 'pending' }))).toBe(true)
    expect(isRunning(mkJob({ status: 'running' }))).toBe(true)
  })

  it('succeeded / failed → false', () => {
    expect(isRunning(mkJob({ status: 'succeeded' }))).toBe(false)
    expect(isRunning(mkJob({ status: 'failed' }))).toBe(false)
  })
})

describe('isTerminal', () => {
  it('null / undefined → false', () => {
    expect(isTerminal(null)).toBe(false)
    expect(isTerminal(undefined)).toBe(false)
  })

  it('pending / running → false', () => {
    expect(isTerminal(mkJob({ status: 'pending' }))).toBe(false)
    expect(isTerminal(mkJob({ status: 'running' }))).toBe(false)
  })

  it('succeeded / failed → true', () => {
    expect(isTerminal(mkJob({ status: 'succeeded' }))).toBe(true)
    expect(isTerminal(mkJob({ status: 'failed' }))).toBe(true)
  })
})

describe('terminalOutcome', () => {
  it('非终态 → null', () => {
    expect(terminalOutcome(mkJob({ status: 'running' }))).toBeNull()
    expect(terminalOutcome(mkJob({ status: 'pending' }))).toBeNull()
  })

  it('succeeded → "succeeded"', () => {
    expect(terminalOutcome(mkJob({ status: 'succeeded' }))).toBe('succeeded')
  })

  it('failed → "failed"', () => {
    expect(terminalOutcome(mkJob({ status: 'failed' }))).toBe('failed')
  })

  it('null → null', () => {
    expect(terminalOutcome(null)).toBeNull()
  })
})

// ============================================================
// statusLabel / kindLabel / kindTab
// ============================================================

describe('statusLabel', () => {
  it('四种标准状态的中文映射', () => {
    expect(statusLabel('pending')).toBe('排队中')
    expect(statusLabel('running')).toBe('运行中')
    expect(statusLabel('succeeded')).toBe('已完成')
    expect(statusLabel('failed')).toBe('失败')
  })

  it('未知状态 → 原样返回（兜底）', () => {
    expect(statusLabel('something-weird')).toBe('something-weird')
    expect(statusLabel('')).toBe('')
  })
})

describe('kindLabel', () => {
  it('parse → 解析', () => expect(kindLabel('parse')).toBe('解析'))
  it('bid → 编写', () => expect(kindLabel('bid')).toBe('编写'))
  it('supplement → 编写（补写也算编写）', () => expect(kindLabel('supplement')).toBe('编写'))
  it('rewrite → 编写', () => expect(kindLabel('rewrite')).toBe('编写'))
  it('空字符串 → 兜底 "任务"', () => expect(kindLabel('')).toBe('任务'))
})

describe('kindTab', () => {
  it('parse → upload', () => expect(kindTab('parse')).toBe('upload'))
  it('bid → write', () => expect(kindTab('bid')).toBe('write'))
  it('rewrite → write（章节重写也跳编写页）', () => expect(kindTab('rewrite')).toBe('write'))
  it('supplement → write', () => expect(kindTab('supplement')).toBe('write'))
  it('未知 kind → 兜底 write（非 parse 一律跳编写页）', () => expect(kindTab('whatever-new-kind')).toBe('write'))
})

// ============================================================
// filterRunning / hasRunning
// ============================================================

describe('filterRunning', () => {
  it('全部 null → 空数组', () => {
    expect(filterRunning([null, undefined, null])).toEqual([])
  })

  it('混合：只挑 pending/running', () => {
    const parse = mkJob({ kind: 'parse', status: 'pending' })
    const bidRunning = mkJob({ kind: 'bid', status: 'running' })
    const bidDone = mkJob({ kind: 'bid', status: 'succeeded' })
    expect(filterRunning([parse, bidRunning, bidDone, null])).toEqual([parse, bidRunning])
  })

  it('两个都终态 → 空数组', () => {
    const a = mkJob({ status: 'succeeded' })
    const b = mkJob({ status: 'failed' })
    expect(filterRunning([a, b])).toEqual([])
  })
})

describe('hasRunning', () => {
  it('全部 null → false', () => {
    expect(hasRunning([null, null])).toBe(false)
  })

  it('有一个 running → true', () => {
    expect(hasRunning([null, mkJob({ status: 'running' })])).toBe(true)
  })

  it('全部终态 → false', () => {
    expect(hasRunning([mkJob({ status: 'succeeded' }), mkJob({ status: 'failed' })])).toBe(false)
  })
})

// ============================================================
// jobEquals：相同/差异判断
// ============================================================

describe('jobEquals', () => {
  const base = mkJob({ job_id: 'abc', status: 'running', progress: ['a'] })

  it('同一个引用 → true', () => {
    expect(jobEquals(base, base)).toBe(true)
  })

  it('两边都是 null → true', () => {
    expect(jobEquals(null, null)).toBe(true)
  })

  it('一边 null 一边非 null → false', () => {
    expect(jobEquals(null, base)).toBe(false)
    expect(jobEquals(base, null)).toBe(false)
  })

  it('job_id 不同 → false', () => {
    const other = mkJob({ job_id: 'xyz', status: base.status, progress: base.progress })
    expect(jobEquals(base, other)).toBe(false)
  })

  it('status 不同 → false', () => {
    const other = mkJob({ job_id: base.job_id, status: 'succeeded', progress: base.progress })
    expect(jobEquals(base, other)).toBe(false)
  })

  it('progress 长度不同 → false（每加一条进度就要触发重渲染）', () => {
    const other = mkJob({ job_id: base.job_id, status: base.status, progress: ['a', 'b'] })
    expect(jobEquals(base, other)).toBe(false)
  })

  it('其他字段（result / error / bid 等）变化不影响（只关心 poll 频率相关的字段）', () => {
    const other = { ...base, result: { doc_type: 'tender_file' } } as JobView
    expect(jobEquals(base, other)).toBe(true)
  })

  const progress50 = Array.from({ length: 50 }, (_, index) => `msg-${index + 1}`)
  const withProgress = (job: JobView, progress: string[] | null | undefined): JobView => ({
    ...job,
    progress: progress as string[],
  })

  it('50 条窗口等长且前 49 条相同、第 50 条不同 → false（旧实现只比长度会误判 true）', () => {
    const previous = mkJob({ job_id: base.job_id, status: base.status, progress: progress50 })
    const next = mkJob({
      job_id: base.job_id,
      status: base.status,
      progress: [...progress50.slice(0, 49), 'msg-50-updated'],
    })

    expect(previous.progress).toHaveLength(50)
    expect(next.progress).toHaveLength(50)
    expect(jobEquals(previous, next)).toBe(false)
  })

  it('50 条窗口整体滚动（丢弃首条并追加新末条）→ false', () => {
    const previous = mkJob({ job_id: base.job_id, status: base.status, progress: progress50 })
    const scrolled = mkJob({
      job_id: base.job_id,
      status: base.status,
      progress: [...progress50.slice(1), 'msg-51'],
    })

    expect(scrolled.progress).toHaveLength(50)
    expect(jobEquals(previous, scrolled)).toBe(false)
  })

  it('等长且 progress 内容完全相同（不同引用）→ true，避免无意义重渲染', () => {
    const first = mkJob({ job_id: base.job_id, status: base.status, progress: [...progress50] })
    const second = mkJob({ job_id: base.job_id, status: base.status, progress: [...progress50] })

    expect(first.progress).not.toBe(second.progress)
    expect(jobEquals(first, second)).toBe(true)
  })

  it('空数组 vs 空数组 → true；单侧空数组 → false', () => {
    const firstEmpty = mkJob({ job_id: base.job_id, status: base.status, progress: [] })
    const secondEmpty = mkJob({ job_id: base.job_id, status: base.status, progress: [] })
    const nonEmpty = mkJob({ job_id: base.job_id, status: base.status, progress: ['msg-1'] })

    expect(jobEquals(firstEmpty, secondEmpty)).toBe(true)
    expect(jobEquals(firstEmpty, nonEmpty)).toBe(false)
    expect(jobEquals(nonEmpty, secondEmpty)).toBe(false)
  })

  it('progress 为 null/undefined 时按空窗口兜底，不抛异常并保持现状判等', () => {
    const nullProgress = withProgress(base, null)
    const undefinedProgress = withProgress(base, undefined)
    const emptyProgress = mkJob({ job_id: base.job_id, status: base.status, progress: [] })
    const nonEmpty = mkJob({ job_id: base.job_id, status: base.status, progress: ['msg-1'] })

    expect(jobEquals(nullProgress, undefinedProgress)).toBe(true)
    expect(jobEquals(nullProgress, emptyProgress)).toBe(true)
    expect(jobEquals(undefinedProgress, nonEmpty)).toBe(false)
  })
})

// ============================================================
// pickJobForKind
// ============================================================

describe('pickJobForKind', () => {
  it('kind 匹配 → 返回任务', () => {
    const parse = mkJob({ kind: 'parse' })
    const bid = mkJob({ kind: 'bid' })
    const jobs = { parse, bid } as const
    expect(pickJobForKind(jobs, 'parse')).toBe(parse)
    expect(pickJobForKind(jobs, 'bid')).toBe(bid)
  })

  it('对应 kind 为 null → 返回 null', () => {
    const jobs = { parse: null, bid: null } as const
    expect(pickJobForKind(jobs, 'parse')).toBeNull()
  })

  it('jobs 是 Partial 缺 key → 返回 null', () => {
    const jobs: Partial<Record<'parse' | 'bid', JobView | null>> = { parse: mkJob() }
    expect(pickJobForKind(jobs, 'bid')).toBeNull()
  })
})

// ============================================================
// decidePollOutcome：终态轮询决策（T-030：终态即停、迁移信号、结果留存）
// ============================================================

describe('decidePollOutcome', () => {
  it('运行中观测：不停止轮询、不产生终态迁移', () => {
    const prev = mkJob({ job_id: 'j1', status: 'running' })
    const next = mkJob({ job_id: 'j1', status: 'running', progress: ['a', 'b'] })
    expect(decidePollOutcome(prev, next)).toEqual({
      stopPolling: false,
      terminalTransition: false,
    })
  })

  it('running → succeeded：停止轮询并报告终态迁移', () => {
    const prev = mkJob({ job_id: 'j1', status: 'running' })
    const next = mkJob({ job_id: 'j1', status: 'succeeded' })
    expect(decidePollOutcome(prev, next)).toEqual({
      stopPolling: true,
      terminalTransition: true,
    })
  })

  it('pending → failed：停止轮询并报告终态迁移', () => {
    const prev = mkJob({ job_id: 'j1', status: 'pending' })
    const next = mkJob({ job_id: 'j1', status: 'failed' })
    expect(decidePollOutcome(prev, next)).toEqual({
      stopPolling: true,
      terminalTransition: true,
    })
  })

  it('首帧即终态（prev 为 null）：停止轮询，且视为一次迁移（快速完成任务）', () => {
    const next = mkJob({ job_id: 'j1', status: 'succeeded' })
    expect(decidePollOutcome(null, next)).toEqual({
      stopPolling: true,
      terminalTransition: true,
    })
  })

  it('prev 为 undefined 同样按首帧处理', () => {
    const next = mkJob({ job_id: 'j1', status: 'failed' })
    expect(decidePollOutcome(undefined, next).terminalTransition).toBe(true)
  })

  it('终态 → 终态（同一终态被再次观测）：停止但不重复报告迁移', () => {
    const prev = mkJob({ job_id: 'j1', status: 'succeeded' })
    const next = mkJob({ job_id: 'j1', status: 'succeeded' })
    expect(decidePollOutcome(prev, next)).toEqual({
      stopPolling: true,
      terminalTransition: false,
    })
  })
})

// ============================================================
// nextHistoryRefreshNonce：历史页刷新信号（T-030，无迁移不自增 → 无空转）
// ============================================================

describe('nextHistoryRefreshNonce', () => {
  it('发生终态迁移：nonce +1', () => {
    expect(nextHistoryRefreshNonce(0, true)).toBe(1)
    expect(nextHistoryRefreshNonce(4, true)).toBe(5)
  })

  it('未发生迁移：nonce 原样返回，连续调用也不变', () => {
    expect(nextHistoryRefreshNonce(3, false)).toBe(3)
    expect(nextHistoryRefreshNonce(0, false)).toBe(0)
  })
})

// ============================================================
// shouldEmitJobSuccess：成功回调按 jobId 触发（T-030 修复 fireRef 闩锁）
// ============================================================

describe('shouldEmitJobSuccess', () => {
  const selected = 'job-ok-1'
  const succeeded = mkJob({ job_id: selected, status: 'succeeded' })

  it('id 匹配、succeeded、未对该 jobId 触发过 → true', () => {
    expect(shouldEmitJobSuccess(succeeded, selected, null)).toBe(true)
    expect(shouldEmitJobSuccess(succeeded, selected, 'job-old')).toBe(true)
  })

  it('已对当前 jobId 触发过 → false（同一任务不重复刷新计数）', () => {
    expect(shouldEmitJobSuccess(succeeded, selected, selected)).toBe(false)
  })

  it('activeJob 与选择态 id 不匹配 → false（防串台）', () => {
    const stale = mkJob({ job_id: 'job-old', status: 'succeeded' })
    expect(shouldEmitJobSuccess(stale, selected, null)).toBe(false)
  })

  it('仍在运行 → false', () => {
    const running = mkJob({ job_id: selected, status: 'running' })
    expect(shouldEmitJobSuccess(running, selected, null)).toBe(false)
  })

  it('activeJob 或 selectedJobId 为空 → false', () => {
    expect(shouldEmitJobSuccess(null, selected, null)).toBe(false)
    expect(shouldEmitJobSuccess(succeeded, null, null)).toBe(false)
  })

  it('复位后新任务成功：旧 jobId 已触发过，新 jobId 仍可再次触发', () => {
    const nextJob = mkJob({ job_id: 'job-ok-2', status: 'succeeded' })
    expect(shouldEmitJobSuccess(nextJob, 'job-ok-2', selected)).toBe(true)
  })
})

// ============================================================
// derivePageJobState：受控选择态 + activeJob 的三态派生（T-031）
// ============================================================

describe('derivePageJobState', () => {
  const job = mkJob({ job_id: 'job-a', kind: 'bid', status: 'running' })

  it('未选择任务 → form（无论 activeJob 是否存在）', () => {
    expect(derivePageJobState(null, null)).toEqual({ mode: 'form' })
    expect(derivePageJobState(null, job)).toEqual({ mode: 'form' })
  })

  it('已选择但首帧 poll 未返回（activeJob 为 null）→ submitting，保留 jobId', () => {
    expect(derivePageJobState('job-a', null)).toEqual({ mode: 'submitting', jobId: 'job-a' })
  })

  it('job_id 匹配 → job，返回同一个 activeJob 引用', () => {
    expect(derivePageJobState('job-a', job)).toEqual({ mode: 'job', job })
  })

  it('activeJob 属于旧任务（id 不匹配）→ submitting，绝不返回旧 job 数据', () => {
    const stale = mkJob({ job_id: 'job-old', status: 'succeeded' })
    expect(derivePageJobState('job-a', stale)).toEqual({ mode: 'submitting', jobId: 'job-a' })
  })
})

// ============================================================
// planJobJump：「查看」按钮跳转决策（T-031）
// ============================================================

describe('planJobJump', () => {
  it('parse 且当前就在 upload tab → 目标 upload，不切 tab（走原地聚焦）', () => {
    expect(planJobJump('parse', 'upload')).toEqual({ tab: 'upload', changedTab: false })
  })

  it('parse 且当前在 write tab → 切到 upload', () => {
    expect(planJobJump('parse', 'write')).toEqual({ tab: 'upload', changedTab: true })
  })

  it('bid 在 upload tab → 切到 write', () => {
    expect(planJobJump('bid', 'upload')).toEqual({ tab: 'write', changedTab: true })
  })

  it('supplement / rewrite 在 upload tab → write（锁定现有去向）', () => {
    expect(planJobJump('supplement', 'upload')).toEqual({ tab: 'write', changedTab: true })
    expect(planJobJump('rewrite', 'upload')).toEqual({ tab: 'write', changedTab: true })
  })

  it('bid 已在 write tab → 不切 tab，原地聚焦', () => {
    expect(planJobJump('bid', 'write')).toEqual({ tab: 'write', changedTab: false })
  })

  it('history/library 等非目标 tab 点击查看 → 必然发生 tab 切换', () => {
    expect(planJobJump('parse', 'history')).toEqual({ tab: 'upload', changedTab: true })
    expect(planJobJump('bid', 'library')).toEqual({ tab: 'write', changedTab: true })
  })
})
