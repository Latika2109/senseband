import { useEffect } from 'react'
import { activeCountdown, countdownLeft } from '../api/live'
import { useT } from '../i18n'
import { useApp } from '../state/AppContext'
import { Button } from './ui'

/** Full-width "Are you OK?" banner while a critical alert is inside its cancel window. */
export function AlertBanner() {
  const { t } = useT()
  const { live, client, now, p } = useApp()
  const a = activeCountdown(live)
  const left = a ? countdownLeft(a, now) : null
  const id = a?.id
  useEffect(() => {
    if (id && 'vibrate' in navigator) navigator.vibrate([300, 150, 300, 150, 300])
  }, [id])
  if (!a || left == null) return null
  const by = p.profile.name || 'app'
  return (
    <div role="alertdialog" aria-labelledby="cd-title" aria-describedby="cd-body"
      className="fixed inset-x-0 top-0 z-50 border-b-4 border-crit bg-surface p-4 shadow-lg">
      <div className="mx-auto flex max-w-xl flex-col gap-3">
        <h2 id="cd-title" className="text-xl font-bold text-crit">▲ {t('alert.countdown.title')}</h2>
        <p id="cd-body">{a.message}</p>
        <p className="text-lg font-semibold tabular-nums" aria-live="polite">{t('alert.countdown.left', { s: left })}</p>
        <Button className="min-h-16 w-full text-xl" onClick={() => void client?.imOk(by)}>{t('alert.imok')}</Button>
      </div>
    </div>
  )
}
