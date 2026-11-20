import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { LangSwitch, Page } from '../components/Page'
import { Badge, Button, Card, Field } from '../components/ui'
import { CloudApi, type CloudCase, type CloudEvent, type CloudNote, type CloudPlan, type CloudPlanItem, type Overview, type Role, type TeamMember, type VitalsRow } from '../cloud/types'
import { useCloud } from '../cloud/CloudContext'
import { inviteCodeOk, latestPlan, limitsOf, nextVersion, otpPhone, SEVERITY, triage, validatePlanEdit } from '../cloud/logic'
import { useT, type Key } from '../i18n'
import { useApp } from '../state/AppContext'

const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function CloudLogin() {
  const { t } = useT()
  const nav = useNavigate()
  const { api, configured, userId, profile, refresh } = useCloud()
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [sent, setSent] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('caregiver')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const full = otpPhone(phone)
  if (!configured) return <Page title={t('cloud.signin')}><Card>{t('cloud.notconfigured')}</Card></Page>
  if (userId && profile?.name) return <Navigate to="/cloud/patients" replace />
  const run = async (fn: () => Promise<void>) => { setBusy(true); setErr(null); try { await fn() } catch (e) { setErr(msg(e)) } finally { setBusy(false) } }
  if (userId && !profile?.name) {
    return (
      <Page title={t('cloud.name')} back={false}>
        <Field label={t('cloud.name')} value={name} onChange={(e) => setName(e.target.value)} />
        <fieldset className="space-y-2"><legend className="font-medium">{t('cloud.role')}</legend>
          {(['caregiver', 'doctor', 'patient'] as Role[]).map((r) => (
            <label key={r} className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3"><input type="radio" name="role" checked={role === r} onChange={() => setRole(r)} className="h-5 w-5" />{t(`cloud.role.${r}` as Key)}</label>
          ))}</fieldset>
        {err && <p role="alert" className="text-crit">{err}</p>}
        <Button className="w-full" disabled={busy || name.trim().length < 2} onClick={() => void run(async () => { await api!.saveProfile(name.trim(), role); await refresh(); nav('/cloud/patients', { replace: true }) })}>{t('cloud.save')}</Button>
      </Page>
    )
  }
  return (
    <Page title={t('cloud.signin')} sub={t('cloud.signin.sub')}>
      <Field label={t('cloud.phone')} inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} disabled={sent} error={phone && !full ? t('cloud.err.phone') : undefined} />
      {!sent ? (
        <Button className="w-full" disabled={!full || busy} onClick={() => void run(async () => { await api!.sendOtp(full!); setSent(true) })}>{t('cloud.sendcode')}</Button>
      ) : (<>
        <Field label={t('cloud.code')} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
        <Button className="w-full" disabled={busy || !/^\d{6}$/.test(code)} onClick={() => void run(async () => { await api!.verifyOtp(full!, code); await refresh() })}>{t('cloud.verify')}</Button>
      </>)}
      {err && <p role="alert" className="text-crit">{err}</p>}
    </Page>
  )
}

function Toasts() {
  const { t } = useT()
  const { toasts, dismiss } = useCloud()
  return (
    <div className="fixed inset-x-0 top-0 z-50 space-y-2 p-3" aria-live="assertive">
      {toasts.map((e) => (
        <div key={e.id} role="alert" className={`mx-auto max-w-xl rounded-xl border-2 bg-surface p-3 shadow-lg ${e.level === 'critical' ? 'border-crit' : 'border-warn'}`}>
          <div className="flex items-start justify-between gap-3"><p><b>{t('cloud.live.new', { level: t(`alert.level.${e.level}` as Key), message: e.message ?? e.kind })}</b></p>
            <button type="button" className="text-muted" aria-label={t('common.close')} onClick={() => dismiss(e.id)}>✕</button></div>
        </div>
      ))}
    </div>
  )
}

export function CloudShell({ children, title }: { children: React.ReactNode; title: string }) {
  const { t } = useT()
  const { signOut, profile } = useCloud()
  const nav = useNavigate()
  return (
    <main className="mx-auto max-w-xl space-y-4 px-4 pb-10 pt-4">
      <Toasts />
      <header className="flex items-center justify-between"><h1 className="text-2xl font-bold">{title}</h1>
        <div className="flex items-center gap-2"><LangSwitch /><Button variant="ghost" className="!min-h-10 !px-2" onClick={() => void signOut().then(() => nav('/cloud/login'))}>{t('cloud.signout')}</Button></div></header>
      {profile?.name && <p className="text-sm text-muted">{profile.name}</p>}
      {children}
      <p className="text-xs text-muted">{t('disclaimer')}</p>
    </main>
  )
}

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): [T | null, () => void, string | null] {
  const [v, setV] = useState<T | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { let alive = true; fn().then((x) => alive && setV(x)).catch((e) => alive && setErr(msg(e))); return () => { alive = false } }, [...deps, tick])
  return [v, () => setTick((x) => x + 1), err]
}

export function CloudPatients() {
  const { t } = useT()
  const { api, userId, ready, enablePush, toasts } = useCloud()
  const [rows, reload, err] = useLoad<Overview[]>(() => api!.overview(), [api, userId, toasts.length])
  const [code, setCode] = useState('')
  const [cerr, setCerr] = useState<string | null>(null)
  const [push, setPush] = useState<string | null>(null)
  if (ready && (!api || !userId)) return <Navigate to="/cloud/login" replace />
  const list = rows ? triage(rows) : []
  return (
    <CloudShell title={t('cloud.patients.title')}>
      {err && <p role="alert" className="text-crit">{err}</p>}
      {rows && list.length === 0 && <Card>{t('cloud.patients.none')}</Card>}
      <ul className="space-y-3">
        {list.map((p) => {
          const sev = SEVERITY[p.open_severity] ?? SEVERITY[0]!
          return (
            <li key={p.id}><Link to={`/cloud/patient/${p.id}`} className="block rounded-2xl border border-line bg-surface p-4">
              <div className="flex items-center justify-between"><span className="text-lg font-semibold">{p.name}</span><Badge level={sev.level}>{t(sev.key)}</Badge></div>
              {p.open_alerts > 0 && <p className="text-sm text-crit">{t('cloud.open_alerts', { n: p.open_alerts })}</p>}
              <p className="text-sm text-muted">{p.last_vitals ? t('cloud.lastvitals', { hr: Math.round(p.last_vitals.hr_ecg ?? p.last_vitals.hr_ppg ?? 0) || '–', spo2: Math.round(p.last_vitals.spo2_avg ?? 0) || '–', activity: p.last_vitals.activity ?? '–' }) : t('cloud.novitals')}</p>
            </Link></li>
          )
        })}
      </ul>
      <Card className="space-y-2"><Field label={t('cloud.team.accept')} value={code} onChange={(e) => setCode(e.target.value)} error={cerr ?? undefined} autoCapitalize="characters" />
        <Button variant="secondary" className="w-full" onClick={() => { setCerr(null); if (!inviteCodeOk(code)) { setCerr(t('cloud.err.invite')); return } void api!.acceptInvite(code.trim()).then(() => { setCode(''); reload() }).catch((e) => setCerr(msg(e))) }}>{t('cloud.team.join')}</Button></Card>
      <Button variant="secondary" className="w-full" onClick={() => void enablePush().then((r) => setPush(r))}>{t('cloud.push.enable')}</Button>
      {push === 'on' && <p role="status">{t('cloud.push.on')}</p>}{push === 'denied' && <p role="alert">{t('cloud.push.denied')}</p>}
    </CloudShell>
  )
}

type Tab = 'overview' | 'alerts' | 'plan' | 'notes' | 'team'

export function CloudPatient() {
  const { t } = useT()
  const { id = '' } = useParams()
  const { api, userId, ready, profile } = useCloud()
  const [tab, setTab] = useState<Tab>('overview')
  const [ov] = useLoad<Overview[]>(() => api!.overview(), [api, userId])
  if (ready && (!api || !userId)) return <Navigate to="/cloud/login" replace />
  const me = ov?.find((p) => p.id === id)
  const isDoctor = profile?.role_default === 'doctor'
  return (
    <CloudShell title={me?.name ?? '…'}>
      <Link to="/cloud/patients" className="text-brand">← {t('cloud.back')}</Link>
      <div role="tablist" className="flex gap-1 overflow-x-auto">
        {(['overview', 'alerts', 'plan', 'notes', 'team'] as Tab[]).map((k) => (
          <button key={k} role="tab" aria-selected={tab === k} type="button" onClick={() => setTab(k)} className={`whitespace-nowrap rounded-full px-3 py-1.5 text-sm font-semibold ${tab === k ? 'bg-brand text-white dark:text-black' : 'border border-line bg-surface'}`}>{t(`cloud.tab.${k}` as Key)}</button>
        ))}
      </div>
      {api && tab === 'overview' && <OverviewTab api={api} id={id} />}
      {api && tab === 'alerts' && <AlertsTab api={api} id={id} />}
      {api && tab === 'plan' && <PlanTab api={api} id={id} editable={isDoctor} />}
      {api && tab === 'notes' && <NotesTab api={api} id={id} canWrite={isDoctor} />}
      {api && tab === 'team' && <TeamTab api={api} id={id} />}
    </CloudShell>
  )
}

function OverviewTab({ api, id }: { api: CloudApi; id: string }) {
  const { t } = useT()
  const [rows] = useLoad<VitalsRow[]>(() => api.vitals(id, Date.now() - 24 * 3600_000), [api, id])
  const data = (rows ?? []).map((r) => ({ t: new Date(r.minute_ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), hr: r.hr_ecg, expected: r.hr_expected }))
  return (
    <Card><h2 className="mb-2 font-semibold">{t('cloud.hrtrend')}</h2>
      {data.length ? <div className="h-52" role="img" aria-label={t('cloud.hrtrend')}><ResponsiveContainer><LineChart data={data}><CartesianGrid stroke="rgb(var(--line))" strokeDasharray="3 3" /><XAxis dataKey="t" minTickGap={40} tick={{ fontSize: 11, fill: 'rgb(var(--muted))' }} /><YAxis width={34} domain={['dataMin - 10', 'dataMax + 10']} tick={{ fontSize: 11, fill: 'rgb(var(--muted))' }} /><Tooltip />
        <Line dataKey="expected" stroke="rgb(var(--muted))" strokeDasharray="5 4" dot={false} isAnimationActive={false} /><Line dataKey="hr" stroke="rgb(var(--brand))" strokeWidth={2.5} dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div> : <p>{t('cloud.novitals')}</p>}</Card>
  )
}

function StripPlot({ samples }: { samples: number[] }) {
  const lo = Math.min(...samples), hi = Math.max(...samples), span = Math.max(hi - lo, 0.5)
  const pts = samples.map((v, i) => `${(i / (samples.length - 1)) * 300},${58 - ((v - lo) / span) * 54}`).join(' ')
  return <svg viewBox="0 0 300 60" className="h-20 w-full rounded-lg bg-bg" role="img" aria-label="ECG strip"><polyline points={pts} fill="none" stroke="rgb(var(--brand))" strokeWidth="1.2" /></svg>
}

function AlertsTab({ api, id }: { api: CloudApi; id: string }) {
  const { t } = useT()
  const [rows, reload] = useLoad<CloudEvent[]>(() => api.events(id), [api, id])
  const [open, setOpen] = useState<string | null>(null)
  const [strip, setStrip] = useState<number[] | null | 'none'>(null)
  const show = async (e: CloudEvent) => { if (open === e.id) { setOpen(null); return } setOpen(e.id); setStrip(null); setStrip((await api.ecgStrip(e.id).catch(() => null)) ?? 'none') }
  if (rows && !rows.length) return <Card>{t('alert.none')}</Card>
  return (
    <ul className="space-y-3">{(rows ?? []).map((e) => (
      <li key={e.id}><Card className="space-y-2">
        <div className="flex items-center justify-between"><Badge level={e.level}>{t(`alert.level.${e.level}` as Key)}</Badge><time className="text-xs text-muted">{new Date(e.ts).toLocaleString()}</time></div>
        <p>{e.message ?? e.kind}</p>
        {e.status === 'sms_sent' && <p className="text-sm text-muted">{t('alert.sms.sent')}</p>}{e.status === 'cancelled' && <p className="text-sm text-muted">{t('alert.cancelled')}</p>}
        {e.acknowledged_by && <p className="text-sm text-muted">{t('alert.acked', { by: e.acknowledged_by })}</p>}
        <div className="flex gap-2">{!e.acknowledged_by && <Button variant="secondary" className="!min-h-10 !py-1" onClick={() => void api.ack(e.id).then(reload)}>{t('alert.ack')}</Button>}
          {e.level !== 'info' && <Button variant="ghost" className="!min-h-10 !py-1" onClick={() => void show(e)}>{t('alert.strip')}</Button>}</div>
        {open === e.id && (strip === 'none' ? <p className="text-sm text-muted">{t('alert.nostrip')}</p> : strip ? <StripPlot samples={strip} /> : <p className="text-sm text-muted">{t('common.loading')}</p>)}
      </Card></li>))}</ul>
  )
}

function PlanTab({ api, id, editable }: { api: CloudApi; id: string; editable: boolean }) {
  const { t } = useT()
  const [plans, reloadPlans] = useLoad<CloudPlan[]>(() => api.plans(id), [api, id])
  const [cases, reloadCases] = useLoad<CloudCase[]>(() => api.cases(id), [api, id])
  const plan = plans ? latestPlan(plans) : null
  const limits = useMemo(() => (cases ? limitsOf(cases) : {}), [cases])
  const [items, setItems] = useState<CloudPlanItem[] | null>(null)
  const [lim, setLim] = useState<Record<string, string>>({})
  const [done, setDone] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { if (plan) setItems(plan.items_json.items.map((i) => ({ ...i }))) }, [plan?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setLim(Object.fromEntries(Object.entries(limits).map(([k, v]) => [k, String(v)]))) }, [cases]) // eslint-disable-line react-hooks/exhaustive-deps
  const errors = useMemo(() => (items ? validatePlanEdit(items, limits) : {}), [items, limits])
  const set = (i: number, patch: Partial<CloudPlanItem>) => setItems((cur) => cur!.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const save = useCallback(async () => {
    setErr(null); setDone(null)
    try { await api.savePlan(id, null, plan?.stage ?? '1', items!, plan?.items_json.movement, nextVersion(plans ?? [])); setDone('plan'); reloadPlans() } catch (e) { setErr(msg(e)) }
  }, [api, id, items, plan, plans, reloadPlans])
  const saveLimits = async () => {
    setErr(null)
    try {
      const out: Record<string, number> = {}
      for (const [k, v] of Object.entries(lim)) if (v.trim() && Number(v) >= 10 && Number(v) <= 200) out[k] = Number(v)
      if (cases?.[0]) await api.setLimits(cases[0].id, out)
      setDone('limits'); reloadCases()
    } catch (e) { setErr(msg(e)) }
  }
  return (
    <div className="space-y-4">
      {cases?.[0] && (
        <Card className="space-y-2"><h2 className="font-semibold">{t('cloud.limits.title')}</h2>
          {Object.keys(lim).length === 0 && <p className="text-muted">{t('cloud.limits.none')}</p>}
          {Object.entries(lim).map(([k, v]) => <Field key={k} label={t(`movement.${k}` as Key)} inputMode="numeric" value={v} disabled={!editable} onChange={(e) => setLim({ ...lim, [k]: e.target.value })} />)}
          {editable && Object.keys(lim).length > 0 && <Button variant="secondary" onClick={() => void saveLimits()}>{t('cloud.limits.save')}</Button>}
          {done === 'limits' && <p role="status">{t('cloud.limits.saved')}</p>}</Card>)}
      {!plan && <Card>{t('cloud.plan.none')}</Card>}
      {plan && items && (
        <Card className="space-y-3">
          <p className="text-sm text-muted">{t('cloud.plan.version', { v: plan.version, by: plan.created_by })}{plan.approved_by ? ` · ${t('cloud.plan.approved', { by: plan.approved_by })}` : ''}</p>
          {items.map((it, i) => (
            <div key={it.exercise_id} className="space-y-2 rounded-xl border border-line p-3"><h3 className="font-semibold">{it.exercise_id.replaceAll('_', ' ')}</h3>
              <div className="grid grid-cols-4 gap-2">
                {([['sets', 'cloud.plan.sets'], ['reps', 'cloud.plan.reps'], ['target_angle', 'cloud.plan.angle'], ['hold_s', 'cloud.plan.hold']] as const).map(([f, k]) => (
                  <Field key={f} label={t(k)} inputMode="numeric" disabled={!editable} value={String(it[f])} onChange={(e) => set(i, { [f]: Number(e.target.value) || 0 } as Partial<CloudPlanItem>)} />))}
              </div>
              {errors[i] && <p role="alert" className="text-sm text-crit">{t(errors[i]!)}</p>}</div>))}
          {editable && <Button className="w-full" disabled={Object.keys(errors).length > 0} onClick={() => void save()}>{t('cloud.plan.save')}</Button>}
          {done === 'plan' && <p role="status">{t('cloud.plan.saved')}</p>}
        </Card>)}
      {err && <p role="alert" className="text-crit">{err}</p>}
    </div>
  )
}

function NotesTab({ api, id, canWrite }: { api: CloudApi; id: string; canWrite: boolean }) {
  const { t } = useT()
  const [notes, reload] = useLoad<CloudNote[]>(() => api.notes(id), [api, id])
  const [text, setText] = useState('')
  return (
    <div className="space-y-3">
      {canWrite && (<Card className="space-y-2"><label htmlFor="note" className="sr-only">{t('cloud.note.add')}</label>
        <textarea id="note" value={text} onChange={(e) => setText(e.target.value)} placeholder={t('cloud.note.placeholder')} rows={3} className="w-full rounded-xl border border-line bg-surface p-3" />
        <Button disabled={!text.trim()} onClick={() => void api.addNote(id, text.trim()).then(() => { setText(''); reload() })}>{t('cloud.note.add')}</Button></Card>)}
      {notes && !notes.length && <Card>{t('cloud.note.none')}</Card>}
      {(notes ?? []).map((n) => <Card key={n.id}><p>{n.text}</p><p className="mt-1 text-xs text-muted">{n.author_name} · {new Date(n.ts).toLocaleString()}</p></Card>)}
    </div>
  )
}

function TeamTab({ api, id }: { api: CloudApi; id: string }) {
  const { t } = useT()
  const { userId } = useCloud()
  const [team, reload] = useLoad<TeamMember[]>(() => api.team(id), [api, id])
  const [code, setCode] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const inv = async (role: 'caregiver' | 'doctor') => { setErr(null); try { setCode(await api.invite(id, role)) } catch (e) { setErr(msg(e)) } }
  return (
    <div className="space-y-3">
      <ul className="space-y-2">{(team ?? []).filter((m) => m.status !== 'invited').map((m) => (
        <li key={m.user_id}><Card className="flex items-center justify-between"><div><p className="font-semibold">{m.name ?? '—'}</p><p className="text-sm text-muted">{t(`cloud.role.${m.role}` as Key)}{m.status === 'revoked' ? ` · ${t('cloud.team.revoked')}` : ''}</p></div>
          {m.status === 'active' && m.role !== 'patient' && m.user_id !== userId && <Button variant="secondary" className="!min-h-10 !py-1" onClick={() => void api.revoke(id, m.user_id).then(reload).catch((e) => setErr(msg(e)))}>{t('cloud.team.revoke')}</Button>}</Card></li>))}</ul>
      <div className="grid grid-cols-2 gap-3"><Button variant="secondary" onClick={() => void inv('caregiver')}>{t('cloud.team.invite.caregiver')}</Button><Button variant="secondary" onClick={() => void inv('doctor')}>{t('cloud.team.invite.doctor')}</Button></div>
      {code && <Card><p role="status" className="text-lg font-bold tracking-widest">{t('cloud.team.code', { code })}</p></Card>}
      {err && <p role="alert" className="text-crit">{err}</p>}
    </div>
  )
}

/** In the band's Settings: create the patient in the cloud, register the band, give it its key. */
export function LinkBandCard() {
  const { t } = useT()
  const { api, userId, profile } = useCloud()
  const { client, pairInfo } = useApp()
  const [url, setUrl] = useState('')
  const [state, setState] = useState<'idle' | 'busy' | 'ok'>('idle')
  const [err, setErr] = useState<string | null>(null)
  if (!api || !userId) return null
  const run = async () => {
    if (!client) return
    setState('busy'); setErr(null)
    try {
      const pid = pairInfo?.patient_id ?? (await client.patient()).id
      const me = await client.patient()
      const role = profile?.role_default === 'patient' ? 'patient' : 'caregiver'
      await api.createPatient(pid, me.name || 'Patient', role).catch((e) => { if (!/duplicate/i.test(msg(e))) throw e })
      const key = await api.registerDevice(pid, null)
      await client.linkCloud(url.replace(/\/+$/, ''), key)
      setState('ok')
    } catch (e) { setErr(msg(e)); setState('idle') }
  }
  return (
    <Card className="space-y-2"><h2 className="font-semibold">{t('cloud.link.title')}</h2><p className="text-sm text-muted">{t('cloud.link.body')}</p>
      <Field label={t('cloud.link.url')} placeholder="https://xxxx.supabase.co/functions/v1" value={url} onChange={(e) => setUrl(e.target.value)} inputMode="url" />
      {err && <p role="alert" className="text-crit">{err}</p>}{state === 'ok' && <p role="status">{t('cloud.link.ok')}</p>}
      <Button variant="secondary" className="w-full" disabled={state === 'busy' || !/^https?:\/\//.test(url)} onClick={() => void run()}>{t('cloud.link.do')}</Button></Card>
  )
}
