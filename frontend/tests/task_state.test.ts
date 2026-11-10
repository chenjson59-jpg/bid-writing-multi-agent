/** 全局任务状态纯函数的单元测试。
 *
 * 这些函数没有副作用，没有 React 依赖，直接 vitest 跑，不需要 jsdom。
 * 如果某个函数在 App.tsx / GlobalTaskBar 里改了行为，这里必然失败。
 */
import { describe, expect, it, vi } from 'vitest'

import type { JobView } from '../src/types'
import {
  closeJobById,
  decidePollOutcome,
  derivePageJobState,
  filterRunning,
  findTrackedJob,
  hasRunning,
  isRunning,
  isTerminal,
  jobChannel,
  jobEquals,
  kindLabel,
  kindTab,
  markJobSettled,
  nextHistoryRefreshNonce,
  pickJobForKind,
  planJobJump,
  pollableJobs,
  putSettledCallback,
  registerTrackedJob,
  revokeSettledCallback,
  selectionAfterClose,
  selectionAfterRegister,
  selectedJobSnapshot,
  shouldEmitJobSuccess,
  statusLabel,
  takeSettledCallback,
  terminalOutcome,
  upsertJobSnapshot,
} from '../src/lib/task_state'
import type {
  ChannelSelection,
  SettledCallback,
  TrackedJob,
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
  // T-034 决策七：任务栏需区分补写/重写，标签从「编写」细化（断言迁移，非删例）
  it('supplement → 补写（T-034 由「编写」迁移）', () => expect(kindLabel('supplement')).toBe('补写'))
  it('rewrite → 重写（T-034 由「编写」迁移）', () => expect(kindLabel('rewrite')).toBe('重写'))
  it('空字符串 → 兜底 "任务"', () => expect(kindLabel('')).toBe('任务'))
  it('未知 kind → 原样返回（兜底，不臆造标签）', () => expect(kindLabel('future-kind')).toBe('future-kind'))
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

// ============================================================
// T-034 多任务集合（job_id 键控、四 kind、终态留存与显式关闭）
// ============================================================

function mkEntry(
  overrides: Partial<TrackedJob> & Pick<TrackedJob, 'jobId' | 'kind'>,
): TrackedJob {
  return { job: null, settled: false, ...overrides }
}

const emptySelection: ChannelSelection = { upload: null, write: null }

describe('T-034 jobChannel（kind → 页面通道）', () => {
  it('parse → upload 通道', () => {
    expect(jobChannel('parse')).toBe('upload')
  })
  it('bid / supplement / rewrite → write 通道', () => {
    expect(jobChannel('bid')).toBe('write')
    expect(jobChannel('supplement')).toBe('write')
    expect(jobChannel('rewrite')).toBe('write')
  })
  it('未知 kind → 兜底 write（非 parse 一律 write）', () => {
    expect(jobChannel('whatever-new-kind')).toBe('write')
  })
  it('kindTab 与 jobChannel 输出保持一致', () => {
    expect(kindTab('parse')).toBe(jobChannel('parse'))
    expect(kindTab('supplement')).toBe(jobChannel('supplement'))
    expect(kindTab('rewrite')).toBe(jobChannel('rewrite'))
  })
})

describe('T-034 registerTrackedJob（追加、置顶、不覆盖）', () => {
  it('空集合追加：成为唯一条目', () => {
    const next = registerTrackedJob([], mkEntry({ jobId: 'j1', kind: 'parse' }))
    expect(next).toHaveLength(1)
    expect(next[0].jobId).toBe('j1')
  })

  it('连续追加两个不同 id：互不覆盖且新任务置顶', () => {
    const first = registerTrackedJob([], mkEntry({ jobId: 'j1', kind: 'bid' }))
    const second = registerTrackedJob(first, mkEntry({ jobId: 'j2', kind: 'bid' }))
    expect(second.map((e) => e.jobId)).toEqual(['j2', 'j1'])
  })

  it('同类多任务全部保留（旧单槽覆盖根因的回归用例）', () => {
    let jobs: TrackedJob[] = []
    for (const jobId of ['j1', 'j2', 'j3']) {
      jobs = registerTrackedJob(jobs, mkEntry({ jobId, kind: 'bid' }))
    }
    expect(jobs).toHaveLength(3)
    expect(jobs.map((e) => e.jobId)).toEqual(['j3', 'j2', 'j1'])
    expect(jobs.every((e) => e.kind === 'bid')).toBe(true)
  })

  it('同 jobId 重复登记：替换条目并置顶，不产生重复行', () => {
    const first = registerTrackedJob([], mkEntry({ jobId: 'j1', kind: 'bid' }))
    const second = registerTrackedJob(first, mkEntry({ jobId: 'j2', kind: 'parse' }))
    const replaced = registerTrackedJob(
      second,
      mkEntry({ jobId: 'j1', kind: 'bid', job: mkJob({ job_id: 'j1' }), settled: true }),
    )
    expect(replaced).toHaveLength(2)
    expect(replaced.map((e) => e.jobId)).toEqual(['j1', 'j2'])
    expect(replaced[0].settled).toBe(true)
    expect(replaced[0].job?.job_id).toBe('j1')
  })

  it('不可变更新：原数组不被修改', () => {
    const prev = [mkEntry({ jobId: 'j1', kind: 'parse' })]
    const snapshot = [...prev]
    registerTrackedJob(prev, mkEntry({ jobId: 'j2', kind: 'parse' }))
    expect(prev).toEqual(snapshot)
  })
})

describe('T-034 upsertJobSnapshot（按 id 更新快照与渲染 bail-out）', () => {
  const jobs: TrackedJob[] = [
    mkEntry({ jobId: 'j2', kind: 'bid', job: mkJob({ job_id: 'j2', status: 'running' }) }),
    mkEntry({ jobId: 'j1', kind: 'parse', job: mkJob({ job_id: 'j1', status: 'running' }) }),
  ]

  it('按 jobId 更新对应快照，其他条目与顺序不变', () => {
    const nextJob = mkJob({ job_id: 'j1', status: 'succeeded', progress: ['done'] })
    const next = upsertJobSnapshot(jobs, 'j1', nextJob)
    expect(next[1].job).toBe(nextJob)
    expect(next[0]).toBe(jobs[0])
    expect(next.map((e) => e.jobId)).toEqual(['j2', 'j1'])
  })

  it('新快照与旧快照 jobEquals 相等：返回原数组引用（避免无意义重渲染）', () => {
    const same = mkJob({ job_id: 'j2', status: 'running' })
    const next = upsertJobSnapshot(jobs, 'j2', same)
    expect(next).toBe(jobs)
  })

  it('未知 jobId：原样返回同一引用', () => {
    expect(upsertJobSnapshot(jobs, 'missing', mkJob({ job_id: 'missing' }))).toBe(jobs)
  })

  it('条目快照为 null（首帧）：写入快照且数组引用变化', () => {
    const pending = [mkEntry({ jobId: 'j9', kind: 'supplement', job: null })]
    const first = mkJob({ job_id: 'j9', status: 'pending' })
    const next = upsertJobSnapshot(pending, 'j9', first)
    expect(next[0].job).toBe(first)
    expect(next).not.toBe(pending)
  })
})

describe('T-034 markJobSettled（终态即停、快照留存）', () => {
  it('settled false → true，job 快照保留', () => {
    const jobs = [mkEntry({ jobId: 'j1', kind: 'bid', job: mkJob({ job_id: 'j1', status: 'succeeded' }) })]
    const next = markJobSettled(jobs, 'j1')
    expect(next[0].settled).toBe(true)
    expect(next[0].job?.job_id).toBe('j1')
  })
  it('已 settled 再标记：返回原数组引用', () => {
    const jobs = [mkEntry({ jobId: 'j1', kind: 'bid', settled: true })]
    expect(markJobSettled(jobs, 'j1')).toBe(jobs)
  })
  it('未知 jobId：原样返回', () => {
    const jobs = [mkEntry({ jobId: 'j1', kind: 'bid' })]
    expect(markJobSettled(jobs, 'missing')).toBe(jobs)
  })
})

describe('T-034 closeJobById（显式关闭清理）', () => {
  const jobs: TrackedJob[] = [
    mkEntry({ jobId: 'j2', kind: 'bid' }),
    mkEntry({ jobId: 'j1', kind: 'parse', settled: true }),
  ]

  it('移除指定条目，其他条目与顺序保留', () => {
    const next = closeJobById(jobs, 'j1')
    expect(next.map((e) => e.jobId)).toEqual(['j2'])
  })
  it('移除置顶条目后剩余顺序不变', () => {
    expect(closeJobById(jobs, 'j2').map((e) => e.jobId)).toEqual(['j1'])
  })
  it('未知 jobId：返回原数组引用', () => {
    expect(closeJobById(jobs, 'missing')).toBe(jobs)
  })
  it('不可变：原数组不被修改', () => {
    const snapshot = jobs.map((e) => e.jobId)
    closeJobById(jobs, 'j1')
    expect(jobs.map((e) => e.jobId)).toEqual(snapshot)
  })
})

describe('T-034 findTrackedJob / pollableJobs / selectedJobSnapshot', () => {
  const jobs: TrackedJob[] = [
    mkEntry({ jobId: 'j3', kind: 'rewrite', settled: true, job: mkJob({ job_id: 'j3', status: 'succeeded' }) }),
    mkEntry({ jobId: 'j2', kind: 'bid', settled: false, job: mkJob({ job_id: 'j2', status: 'running' }) }),
    mkEntry({ jobId: 'j1', kind: 'parse', settled: false, job: null }),
  ]

  it('findTrackedJob：命中返回同引用，null/未知 id 返回 null', () => {
    expect(findTrackedJob(jobs, 'j2')).toBe(jobs[1])
    expect(findTrackedJob(jobs, null)).toBeNull()
    expect(findTrackedJob(jobs, 'missing')).toBeNull()
  })

  it('pollableJobs：只返回 settled=false 且保持数组顺序', () => {
    expect(pollableJobs(jobs).map((e) => e.jobId)).toEqual(['j2', 'j1'])
  })

  it('全部终态时 pollableJobs 为空数组（全局 interval 不空转请求）', () => {
    const allDone = jobs.map((e) => ({ ...e, settled: true }))
    expect(pollableJobs(allDone)).toEqual([])
  })

  it('selectedJobSnapshot：返回选中条目的同一快照引用', () => {
    expect(selectedJobSnapshot(jobs, 'j2')).toBe(jobs[1].job)
  })
  it('selectedJobSnapshot：选中条目首帧快照为 null → null（对应 submitting 态）', () => {
    expect(selectedJobSnapshot(jobs, 'j1')).toBeNull()
  })
  it('selectedJobSnapshot：selectedId 为 null 或找不到 → null', () => {
    expect(selectedJobSnapshot(jobs, null)).toBeNull()
    expect(selectedJobSnapshot(jobs, 'missing')).toBeNull()
  })
})

describe('T-034 通道选择态 selectionAfterRegister / selectionAfterClose', () => {
  it('登记后对应通道选中新 jobId，另一通道不变', () => {
    const next = selectionAfterRegister(emptySelection, 'write', 'j1')
    expect(next).toEqual({ upload: null, write: 'j1' })
    expect(next).not.toBe(emptySelection)
  })

  it('同通道再次登记：选择态指向最新任务（自动选中置顶任务）', () => {
    const first = selectionAfterRegister(emptySelection, 'write', 'j1')
    const second = selectionAfterRegister(first, 'write', 'j2')
    expect(second.write).toBe('j2')
    expect(second.upload).toBeNull()
  })

  it('关闭的正是当前选中项：该通道回 null（页面回表单），另一通道不变', () => {
    const selected = { upload: 'p1', write: 'j1' }
    expect(selectionAfterClose(selected, 'write', 'j1')).toEqual({ upload: 'p1', write: null })
  })

  it('关闭非选中项：选择态保持不变', () => {
    const selected = { upload: null, write: 'j1' }
    expect(selectionAfterClose(selected, 'write', 'j2')).toBe(selected)
  })

  it('关闭另一通道的任务：本通道选择态不受影响', () => {
    const selected = { upload: 'p1', write: 'j1' }
    expect(selectionAfterClose(selected, 'upload', 'p1')).toEqual({ upload: null, write: 'j1' })
  })
})

describe('T-034 终态回调注册表（一次触发、卸载撤销）', () => {
  it('put 后 take 取到同一函数引用', () => {
    const reg = new Map<string, SettledCallback>()
    const cb = vi.fn()
    putSettledCallback(reg, 'j1', cb)
    expect(takeSettledCallback(reg, 'j1')).toBe(cb)
  })

  it('take 取出即删：重放终态第二次 take 为 undefined（回调只触发一次）', () => {
    const reg = new Map<string, SettledCallback>()
    putSettledCallback(reg, 'j1', vi.fn())
    takeSettledCallback(reg, 'j1')
    expect(takeSettledCallback(reg, 'j1')).toBeUndefined()
  })

  it('revoke 后 take 为 undefined（组件卸载清理，不操作已卸载组件）', () => {
    const reg = new Map<string, SettledCallback>()
    const cb = vi.fn()
    putSettledCallback(reg, 'j1', cb)
    revokeSettledCallback(reg, 'j1')
    expect(takeSettledCallback(reg, 'j1')).toBeUndefined()
    expect(cb).not.toHaveBeenCalled()
  })

  it('同 jobId 再次 put：以最新回调为准（覆盖登记）', () => {
    const reg = new Map<string, SettledCallback>()
    const first = vi.fn()
    const second = vi.fn()
    putSettledCallback(reg, 'j1', first)
    putSettledCallback(reg, 'j1', second)
    expect(takeSettledCallback(reg, 'j1')).toBe(second)
    expect(first).not.toHaveBeenCalled()
  })

  it('不同 jobId 互不干扰，revoke 一个不影响另一个', () => {
    const reg = new Map<string, SettledCallback>()
    const cb1 = vi.fn()
    const cb2 = vi.fn()
    putSettledCallback(reg, 'j1', cb1)
    putSettledCallback(reg, 'j2', cb2)
    revokeSettledCallback(reg, 'j1')
    expect(takeSettledCallback(reg, 'j2')).toBe(cb2)
    expect(takeSettledCallback(reg, 'j1')).toBeUndefined()
  })

  it('对未登记的 jobId 执行 take/revoke 不抛异常', () => {
    const reg = new Map<string, SettledCallback>()
    expect(() => takeSettledCallback(reg, 'x')).not.toThrow()
    expect(() => revokeSettledCallback(reg, 'x')).not.toThrow()
  })
})