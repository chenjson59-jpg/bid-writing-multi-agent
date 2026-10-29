import { useEffect, useRef, type ReactNode } from 'react'

export function Button({
  children,
  onClick,
  variant = 'primary',
  disabled = false,
  type = 'button',
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  disabled?: boolean
  type?: 'button' | 'submit'
  className?: string
}) {
  const styles: Record<string, string> = {
    primary: 'bg-sky-600 text-white hover:bg-sky-500 disabled:bg-slate-600',
    secondary: 'bg-slate-200 text-slate-800 hover:bg-slate-300 dark:bg-slate-700 dark:text-slate-100 dark:hover:bg-slate-600',
    danger: 'bg-rose-600 text-white hover:bg-rose-500 disabled:bg-slate-600',
    ghost: 'bg-transparent text-slate-300 hover:bg-slate-800',
  }
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles[variant]} ${className}`}
    >
      {children}
    </button>
  )
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-5">
      {title && <h3 className="mb-3 text-sm font-semibold text-slate-200">{title}</h3>}
      {children}
    </div>
  )
}

export function Badge({ children, tone = 'muted' }: { children: ReactNode; tone?: 'success' | 'warn' | 'muted' }) {
  const styles: Record<string, string> = {
    success: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    warn: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    muted: 'bg-slate-700/40 text-slate-300 border-slate-600/40',
  }
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${styles[tone]}`}>
      {children}
    </span>
  )
}

export function Banner({ kind, children }: { kind: 'error' | 'warn' | 'info'; children: ReactNode }) {
  const styles: Record<string, string> = {
    error: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  }
  return <div className={`rounded-md border px-4 py-3 text-sm ${styles[kind]}`}>{children}</div>
}

export function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-500 border-t-transparent"
      role="status"
      aria-label="加载中"
    />
  )
}

export function ProgressBar({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent))
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-slate-800"
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="h-full bg-sky-500 transition-all" style={{ width: `${clamped}%` }} />
    </div>
  )
}

export function Field({ label, children, error }: { label: string; children: ReactNode; error?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-slate-400">{label}</span>
      {children}
      {error && <span className="mt-1 block text-xs text-rose-400">{error}</span>}
    </label>
  )
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-700 py-16 text-center">
      <p className="text-slate-300">{title}</p>
      {hint && <p className="mt-1 text-sm text-slate-500">{hint}</p>}
    </div>
  )
}

export function Modal({
  open,
  title,
  onClose,
  children,
  size = 'md',
}: {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
  size?: 'md' | 'lg'
}) {
  const restoreRef = useRef<Element | null>(null)

  useEffect(() => {
    if (!open) return
    restoreRef.current = document.activeElement
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
      if (restoreRef.current instanceof HTMLElement) restoreRef.current.focus()
    }
  }, [open, onClose])

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className={`w-full ${size === 'lg' ? 'max-w-4xl' : 'max-w-lg'} rounded-lg border border-slate-700 bg-slate-900 p-5`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-base font-semibold text-slate-100">{title}</h2>
        {children}
      </div>
    </div>
  )
}
