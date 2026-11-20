import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { AlertBanner } from '../components/AlertBanner'
import { EcgCanvas, HrChart } from '../components/charts'
import { LangSwitch } from '../components/Page'
import { useCloud } from '../cloud/CloudContext'
import { LinkBandCard } from './cloud'
import { Badge, Button, Card, StatusPill, Tile } from '../components/ui'
import { useT, type Key } from '../i18n'
import { useApp } from '../state/AppContext'
import type { AlertInfo, EventRow, Level } from '../api/types'

export function Shell() {
  const { t } = useT()
  const { live, p } = useApp()
  const recovery = p.programType === 'recovery'
  const tab = (to: string, label: Key, icon: string) => (
    <NavLink to={to} className={({ isActive }) => `flex flex-1 flex-col items-center py-2 text-xs ${isActive ? 'font-bold text-brand' : 'text-muted'}`}>
      <span aria-hidden className="text-xl">{icon}</span>{t(label)}
    </NavLink>
  )
  const pending = Object.values(live.alerts).filter((a) => a.level !== 'info' && !a.acknowledged_by && a.status !== 'cancelled').length
  return (
    <div className="mx-auto min-h-screen max-w-xl pb-20">
      <AlertBanner />
      <Outlet />
      <nav aria-label="main" className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface">
        <div className="mx-auto flex max-w-xl">
          {recovery ? tab('/rehab', 'nav.recovery', '🩹') : tab('/live', 'nav.live', '❤')}
          {recovery && tab('/progress', 'nav.progress', '📈')}
          <div className="relative flex flex-1">{tab('/alerts', 'nav.alerts', '🔔')}{pending > 0 && <span className="absolute right-6 top-1 rounded-full bg-crit px-1.5 text-xs font-bold text-white">{pending}</span>}</div>
          {tab('/settings', 'nav.settings', '⚙')}
        </div>
      </nav>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="space-y-2"><h2 className="font-semibold">{title}</h2>{children}</section>
}

export function Dashboard() {
  const { t } = useT()
  const nav = useNavigate()
  const { live, client, resetSetup } = useApp()
  const [notes, setNotes] = useState<{ id: string; author_name: string | null; ts: number; text: string }[]>([])
  useEffect(() => { void client?.notes().then(setNotes).catch(() => undefined) }, [client, live.status?.sync_backlog])
  const v = live.vitals
  const st = live.status
  const learning = !!st?.learning_since_ts && Date.now() - st.learning_since_ts < (st.learning_h ?? 24) * 3600_000
  const activity = t(`activity.${v?.context ?? 'unknown'}` as Key)
  const hrState = useMemo(() => {
    if (!v || v.hr == null) return { state: 'none' as const, note: v?.quality.ecg !== 'good' && v?.quality.ppg !== 'good' ? t('dash.check') : t('dash.nodata') }
    const [lo, hi] = v.hr_range
    if (v.hr > hi) return { state: 'warn' as const, note: t('dash.hr.high', { activity: activity.toLowerCase() }) }
    if (v.hr < lo) return { state: 'warn' as const, note: t('dash.hr.low', { activity: activity.toLowerCase() }) }
    return { state: 'ok' as const, note: t('dash.hr.normal', { activity: activity.toLowerCase() }) }
  }, [v, t, activity])
  const end = async () => {
    if (!client || !confirm(t('dash.stop.confirm'))) return
    if (st?.program) await client.stopProgram(st.program.id).catch(() => undefined)
    resetSetup()
    nav('/', { replace: true })
  }
  return (
    <main className="space-y-4 px-4 pt-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t('dash.title')}</h1>
        <div className="flex items-center gap-2">
          <Badge level={live.conn === 'online' ? 'ok' : 'warning'}>{t(`dash.connection.${live.conn}` as Key)}</Badge>
          <LangSwitch />
        </div>
      </header>
      {live.conn === 'offline' && <Card><p role="status">{t('dash.offline')}</p></Card>}
      {v && <StatusPill status={v.status} label={t(`dash.status.${v.status}` as Key)} />}
      {learning && <Card><p>{t('dash.learning')}</p></Card>}
      <div className="grid grid-cols-2 gap-3">
        <Tile label={t('dash.hr')} value={v?.hr != null ? String(Math.round(v.hr)) : '—'} unit={v?.hr != null ? t('common.bpm') : undefined} state={hrState.state}
          note={v?.hr != null ? `${hrState.note}. ${t('dash.hr.expected', { lo: v.hr_range[0], hi: v.hr_range[1] })}` : hrState.note} />
        <Tile label={t('dash.spo2')} value={v?.spo2 != null ? String(Math.round(v.spo2)) : '—'} unit={v?.spo2 != null ? '%' : undefined}
          state={v?.spo2 == null ? 'none' : v.spo2 < 88 ? 'crit' : v.spo2 < 92 ? 'warn' : 'ok'} note={v?.spo2 == null ? (v?.quality.ppg === 'finger_off' ? t('check.q.finger_off') : t('dash.check')) : undefined} />
        <Tile label={t('dash.temp')} value={v?.temp != null ? v.temp.toFixed(1) : '—'} unit={v?.temp != null ? '°C' : undefined} state={v?.temp == null ? 'none' : v.temp > 38 ? 'warn' : 'ok'} />
        <Tile label={t('dash.rhythm')} value={t(`rhythm.${v?.rhythm ?? 'unknown'}` as Key).split(' – ')[0] ?? ''} state={v?.rhythm === 'possible_irregular' ? 'warn' : 'ok'}
          note={v?.rhythm === 'possible_irregular' ? t('rhythm.possible_irregular').split(' – ')[1] : undefined} />
      </div>
      <Tile label={t('dash.activity')} value={activity} />
      {notes.length > 0 && <Section title={t('cloud.notes.local')}>{notes.slice(0, 3).map((n) => <Card key={n.id}><p>{n.text}</p><p className="mt-1 text-xs text-muted">{n.author_name} · {new Date(n.ts).toLocaleDateString()}</p></Card>)}</Section>}
      <Section title={t('dash.chart.ecg')}><EcgCanvas samples={live.ecg} label={t('dash.chart.ecg')} /></Section>
      <Section title={t('dash.chart.hr')}><Card><HrChart data={live.history} label={t('dash.chart.hr')} /></Card></Section>
      <Button variant="secondary" className="w-full" onClick={end}>{t('dash.stop')}</Button>
      <p className="pb-2 text-xs text-muted">{t('disclaimer')}</p>
    </main>
  )
}

const levelBadge = (l: Level): 'info' | 'warning' | 'critical' => l

type Row = { id: string; ts: number; level: Level; kind: string; message: string; status: string; sms_sent: number; acknowledged_by: string | null }

export function Alerts() {
  const { t } = useT()
  const { client, live, p } = useApp()
  const [rows, setRows] = useState<EventRow[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [strip, setStrip] = useState<{ fs: number; samples: number[] } | null | 'none'>(null)
  const by = p.profile.name || 'app'
  const reload = () => client?.events().then(setRows).catch(() => undefined)
  useEffect(() => { void reload() }, [client, Object.keys(live.alerts).length]) // eslint-disable-line react-hooks/exhaustive-deps

  const merged: Row[] = useMemo(() => {
    const m = new Map<string, Row>(rows.map((r) => [r.id, r]))
    for (const a of Object.values(live.alerts) as (AlertInfo & { rxAt: number })[]) {
      const prev = m.get(a.id)
      m.set(a.id, { id: a.id, ts: prev?.ts ?? Date.now() - Math.max(0, (live.status?.data_time_s ?? 0) - a.raised_t) * 1000, level: a.level, kind: a.kind,
        message: a.message, status: a.status, sms_sent: a.sms_sent, acknowledged_by: a.acknowledged_by ?? prev?.acknowledged_by ?? null })
    }
    return [...m.values()].sort((x, y) => y.ts - x.ts)
  }, [rows, live.alerts, live.status])

  const toggle = async (id: string) => {
    if (open === id) { setOpen(null); return }
    setOpen(id); setStrip(null)
    try { setStrip((await client?.strip(id)) ?? 'none') } catch { setStrip('none') }
  }
  const ack = async (id: string) => { await client?.ack(id, by).catch(() => undefined); await reload() }
  const statusText = (r: Row) => r.status === 'sms_sent' ? t('alert.sms.sent') : r.status === 'sms_failed' ? t('alert.sms.failed') : r.status === 'cancelled' ? t('alert.cancelled') : ''
  return (
    <main className="space-y-3 px-4 pt-4">
      <h1 className="text-2xl font-bold">{t('alert.title')}</h1>
      {merged.length === 0 && <Card><p>{t('alert.none')}</p></Card>}
      <ul className="space-y-3">
        {merged.map((r) => (
          <li key={r.id}><Card className="space-y-2">
            <div className="flex items-center justify-between"><Badge level={levelBadge(r.level)}>{t(`alert.level.${r.level}` as Key)}</Badge>
              <time className="text-xs text-muted" dateTime={new Date(r.ts).toISOString()}>{new Date(r.ts).toLocaleString()}</time></div>
            <p>{r.message}</p>
            {statusText(r) && <p className="text-sm text-muted">{statusText(r)}</p>}
            {r.acknowledged_by && <p className="text-sm text-muted">{t('alert.acked', { by: r.acknowledged_by })}</p>}
            <div className="flex gap-2">
              {!r.acknowledged_by && <Button variant="secondary" className="!min-h-10 !py-1" onClick={() => void ack(r.id)}>{t('alert.ack')}</Button>}
              {r.level !== 'info' && <Button variant="ghost" className="!min-h-10 !py-1" onClick={() => void toggle(r.id)} aria-expanded={open === r.id}>{t('alert.strip')}</Button>}
            </div>
            {open === r.id && (strip === 'none' ? <p className="text-sm text-muted">{t('alert.nostrip')}</p> : strip ? <StripPlot samples={strip.samples} /> : <p className="text-sm text-muted">{t('common.loading')}</p>)}
          </Card></li>
        ))}
      </ul>
    </main>
  )
}

function StripPlot({ samples }: { samples: number[] }) {
  const lo = Math.min(...samples), hi = Math.max(...samples), span = Math.max(hi - lo, 0.5)
  const pts = samples.map((v, i) => `${(i / (samples.length - 1)) * 300},${58 - ((v - lo) / span) * 54}`).join(' ')
  return <svg viewBox="0 0 300 60" className="h-20 w-full rounded-lg bg-bg" role="img" aria-label="ECG strip"><polyline points={pts} fill="none" stroke="rgb(var(--brand))" strokeWidth="1.2" /></svg>
}

export function Settings() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, client, live, forget, pairInfo } = useApp()
  const st = live.status
  const [contacts, setContacts] = useState<{ name: string; phone: string }[]>([])
  useEffect(() => { void client?.patient().then((x) => setContacts(x.emergency_contacts ?? [])).catch(() => undefined) }, [client])
  const sos = async () => { if (confirm(t('alert.sos.confirm'))) await client?.sos().catch(() => undefined) }
  const row = (k: Key, v: string) => <div className="flex justify-between gap-3 py-1"><dt className="text-muted">{t(k)}</dt><dd className="text-right font-medium">{v}</dd></div>
  const { api: cloud, userId } = useCloud()
  const [busy, setBusy] = useState<'report' | 'export' | 'erase' | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const open = (b: Blob, name?: string) => {
    const url = URL.createObjectURL(b)
    if (name) { const a = document.createElement('a'); a.href = url; a.download = name; a.click() } else window.open(url, '_blank')
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }
  const download = async (kind: 'report' | 'export') => {
    if (!client) return
    setBusy(kind); setNote(null)
    try { open(kind === 'report' ? await client.report(st?.program?.type === 'recovery' ? st.program.id : null) : await client.exportData(), kind === 'export' ? 'senseband-data.zip' : undefined) }
    catch (e) { setNote(String(e)) } finally { setBusy(null) }
  }
  const erase = async () => {
    if (!client) return
    const typed = prompt(t('settings.delete.confirm'))
    if (typed !== 'DELETE') return
    setBusy('erase'); setNote(null)
    try {
      const pid = pairInfo?.patient_id
      await client.erase('DELETE')
      let msg = t('settings.erased')
      if (cloud && userId && pid) { await cloud.deletePatient(pid).then(() => { msg += ' ' + t('settings.erase.cloud') }).catch(() => undefined) }
      setNote(msg); forget(); nav('/', { replace: true })
    } catch (e) { setNote(t('settings.erase.failed', { error: String(e) })) } finally { setBusy(null) }
  }
  const doForget = () => { if (confirm(t('settings.forget.confirm'))) { forget(); nav('/', { replace: true }) } }
  return (
    <main className="space-y-4 px-4 pt-4">
      <h1 className="text-2xl font-bold">{t('settings.title')}</h1>
      <Card><div className="flex items-center justify-between"><span className="font-medium">{t('common.language')}</span><LangSwitch /></div></Card>
      <Card><h2 className="mb-1 font-semibold">{t('settings.band')}</h2>
        <dl>{row('settings.address', p.bandUrl ?? '–')}{row('settings.mode', pairInfo?.mode ?? st?.mode ?? '–')}</dl></Card>
      <Card><h2 className="mb-1 font-semibold">{t('settings.device')}</h2>
        <dl>
          {row('settings.battery', st?.band.battery_pct != null ? `${Math.round(st.band.battery_pct)} %` : '–')}
          {row('settings.sms', st ? (st.sms.ok ? t('check.pass') : t('check.fail')) : '–')}
          {row('settings.backlog', st ? String(st.sync_backlog) : '–')}
        </dl></Card>
      <Card><h2 className="mb-1 font-semibold">{t('settings.contacts')}</h2>
        <ul>{contacts.map((c, i) => <li key={i} className="py-0.5">{c.name} · {c.phone}</li>)}</ul></Card>
      <LinkBandCard />
      <Card className="space-y-2">
        <Button variant="secondary" className="w-full" disabled={busy !== null} onClick={() => void download('report')}>{busy === 'report' ? t('settings.report.busy') : t('settings.report')}</Button>
        <Button variant="secondary" className="w-full" disabled={busy !== null} onClick={() => void download('export')}>{busy === 'export' ? t('settings.export.busy') : t('settings.export')}</Button>
        <Button variant="danger" className="w-full" disabled={busy !== null} onClick={() => void erase()}>{t('settings.delete')}</Button>
        {note && <p role="status" className="text-sm">{note}</p>}
      </Card>
      <Button variant="danger" className="w-full" onClick={() => void sos()}>{t('alert.sos')}</Button>
      <Button variant="secondary" className="w-full" onClick={doForget}>{t('settings.forget')}</Button>
      <p className="text-xs text-muted">{t('disclaimer')}</p>
    </main>
  )
}
