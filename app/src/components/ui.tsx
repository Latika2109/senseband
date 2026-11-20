import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { useId } from 'react'

export function Button({ variant = 'primary', className = '', ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }) {
  const base = 'min-h-12 rounded-xl px-5 py-3 font-semibold transition disabled:opacity-40 disabled:cursor-not-allowed active:scale-[0.98]'
  const v = {
    primary: 'bg-brand text-white dark:text-black',
    secondary: 'border border-line bg-surface text-ink',
    danger: 'bg-crit text-white dark:text-black',
    ghost: 'text-brand underline-offset-4 hover:underline',
  }[variant]
  return <button type="button" className={`${base} ${v} ${className}`} {...p} />
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-2xl border border-line bg-surface p-4 ${className}`}>{children}</div>
}

export function Field({ label, error, hint, ...p }: InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; hint?: string }) {
  const id = useId()
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">{label}</label>
      <input id={id} aria-invalid={!!error} aria-describedby={error ? id + 'e' : hint ? id + 'h' : undefined}
        className={`w-full min-h-12 rounded-xl border bg-surface px-3 ${error ? 'border-crit' : 'border-line'}`} {...p} />
      {hint && !error && <p id={id + 'h'} className="text-xs text-muted">{hint}</p>}
      {error && <p id={id + 'e'} role="alert" className="text-xs text-crit">{error}</p>}
    </div>
  )
}

export function Choice({ title, sub, selected, onClick, disabled, icon }: { title: string; sub?: string; selected?: boolean; onClick: () => void; disabled?: boolean; icon?: ReactNode }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-pressed={selected}
      className={`flex w-full items-center gap-3 rounded-2xl border p-4 text-left transition disabled:opacity-50 ${selected ? 'border-brand ring-2 ring-brand' : 'border-line'} bg-surface`}>
      {icon && <span className="text-2xl" aria-hidden>{icon}</span>}
      <span><span className="block font-semibold">{title}</span>{sub && <span className="block text-sm text-muted">{sub}</span>}</span>
    </button>
  )
}

export function Badge({ level, children }: { level: 'info' | 'warning' | 'critical' | 'ok'; children: ReactNode }) {
  const c = { info: 'bg-brand/15 text-brand', warning: 'bg-warn/15 text-warn', critical: 'bg-crit/15 text-crit', ok: 'bg-ok/15 text-ok' }[level]
  return <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${c}`}>{children}</span>
}

/** Status is never colour-only: every state also has an icon and words. */
export function StatusPill({ status, label }: { status: 'normal' | 'watch' | 'alert'; label: string }) {
  const m = { normal: ['bg-ok/15 text-ok', '✓'], watch: ['bg-warn/15 text-warn', '!'], alert: ['bg-crit/15 text-crit', '▲'] }[status]
  return <span role="status" className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ${m[0]}`}><span aria-hidden>{m[1]}</span>{label}</span>
}

export function Tile({ label, value, unit, note, state = 'ok' }: { label: string; value: string; unit?: string; note?: string; state?: 'ok' | 'warn' | 'crit' | 'none' }) {
  const color = { ok: 'text-ink', warn: 'text-warn', crit: 'text-crit', none: 'text-muted' }[state]
  return (
    <Card>
      <p className="text-sm text-muted">{label}</p>
      <p className={`text-4xl font-bold tabular-nums ${color}`}>{value}{unit && <span className="ml-1 text-base font-medium text-muted">{unit}</span>}</p>
      {note && <p className="mt-1 text-sm text-muted">{note}</p>}
    </Card>
  )
}
