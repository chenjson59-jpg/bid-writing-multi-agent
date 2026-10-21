import { useEffect, useRef, useState } from 'react'
import { getJob } from './api'
import type { JobView } from './types'

// 每秒轮询任务直到进入终态；失败时按次退避，连续失败后停止并给出提示。
export function usePolling(jobId: string | null, onDone?: (job: JobView) => void) {
  const [job, setJob] = useState<JobView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      setError(null)
      return
    }

    let cancelled = false
    let failures = 0

    const tick = async () => {
      try {
        const data = await getJob(jobId)
        if (cancelled) return
        setJob(data)
        setError(null)
        failures = 0
        if (data.status === 'succeeded' || data.status === 'failed') {
          onDoneRef.current?.(data)
          return
        }
      } catch (e) {
        if (cancelled) return
        failures += 1
        if (failures >= 3) {
          setError(e instanceof Error ? e.message : '轮询失败')
          return
        }
      }
      const delay = Math.min(1000 * (failures + 1), 5000)
      window.setTimeout(tick, delay)
    }

    tick()
    return () => {
      cancelled = true
    }
  }, [jobId])

  return { job, error }
}

export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw === null ? initial : (JSON.parse(raw) as T)
    } catch {
      return initial
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // 存储不可用时忽略
    }
  }, [key, value])

  return [value, setValue] as const
}
