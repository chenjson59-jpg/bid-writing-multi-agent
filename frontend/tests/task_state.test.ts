/** 全局任务状态纯函数的单元测试。
 *
 * 这些函数没有副作用，没有 React 依赖，直接 vitest 跑，不需要 jsdom。
 * 如果某个函数在 App.tsx / GlobalTaskBar 里改了行为，这里必然失败。
 */
import { describe, expect, it } from 'vitest'

import type { JobView } from '../src/types'
import {
  TERMINAL_GRACE_MS,
  filterRunning,
  hasRunning,
  isRunning,
  isTerminal,
  jobEquals,
  kindLabel,
  kindTab,
  pickJobForKind,
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
// TERMINAL_GRACE_MS 常量
// ============================================================

describe('TERMINAL_GRACE_MS', () => {
  it('8 秒 → 任务终态后 App 还会保留这么久让用户看到完成提示', () => {
    expect(TERMINAL_GRACE_MS).toBe(8_000)
  })
})
