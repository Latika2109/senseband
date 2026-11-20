import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { LANGS, useT } from '../i18n'
import { stepOf } from '../state/flow'
import { Button } from './ui'

export function LangSwitch() {
  const { lang, setLang, t } = useT()
  return (
    <label className="flex items-center gap-2 text-sm text-muted">
      <span className="sr-only">{t('common.language')}</span>
      <select value={lang} onChange={(e) => setLang(e.target.value as 'en' | 'hi')} className="rounded-lg border border-line bg-surface px-2 py-1 text-ink">
        {LANGS.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
      </select>
    </label>
  )
}

export function Page({ title, sub, path, back = true, children, footer }: { title: string; sub?: string; path?: string; back?: boolean; children: ReactNode; footer?: ReactNode }) {
  const { t } = useT()
  const nav = useNavigate()
  const step = path ? stepOf(path) : null
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col px-4 pb-6 pt-4">
      <header className="mb-4 flex items-center justify-between">
        {back ? <Button variant="ghost" className="!min-h-10 !px-0" onClick={() => nav(-1)} aria-label={t('common.back')}>← {t('common.back')}</Button> : <span />}
        {step ? <span className="text-sm text-muted">{t('common.step', step)}</span> : <LangSwitch />}
      </header>
      {step && <div className="mb-4 h-1.5 rounded-full bg-line" aria-hidden><div className="h-full rounded-full bg-brand" style={{ width: `${(step.n / step.total) * 100}%` }} /></div>}
      <h1 className="text-2xl font-bold">{title}</h1>
      {sub && <p className="mt-1 text-muted">{sub}</p>}
      <div className="mt-5 flex-1 space-y-4">{children}</div>
      {footer && <div className="mt-6">{footer}</div>}
    </main>
  )
}
