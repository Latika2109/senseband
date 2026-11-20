import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ApiError } from '../api/client'
import { Gauge } from '../components/Gauge'
import { Page } from '../components/Page'
import { Badge, Button, Card, Choice, Field } from '../components/ui'
import { useT, type Key } from '../i18n'
import { useApp } from '../state/AppContext'
import { ARM_JOINTS, forecastView, MAIN_MOVEMENT, MOVEMENTS_BY_JOINT, NORMAL_ROM, recoveryValid, toRecoveryCase, validateRecovery, type RecoveryDraft } from '../state/recovery'
import type { ExerciseDef, Joint, Plan, PlanItem, Progress, RomResult, SessionResult } from '../api/types'

const JOINTS: Joint[] = ['shoulder', 'elbow', 'wrist', 'hip', 'knee', 'ankle']
const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : String(e))

export function PainScale({ value, onChange, label }: { value: number | null; onChange: (n: number) => void; label: string }) {
  return (
    <fieldset className="space-y-2"><legend className="font-medium">{label}</legend>
      <div className="grid grid-cols-6 gap-2 sm:grid-cols-11" role="radiogroup" aria-label={label}>
        {Array.from({ length: 11 }, (_, i) => (
          <button key={i} type="button" role="radio" aria-checked={value === i} onClick={() => onChange(i)}
            className={`min-h-12 rounded-xl border text-lg font-semibold ${value === i ? 'border-brand bg-brand text-white dark:text-black' : 'border-line bg-surface'} ${i >= 7 ? 'text-crit' : ''}`}>{i}</button>
        ))}
      </div>
    </fieldset>
  )
}

export function RecoveryWhere() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, setP } = useApp()
  const d = p.recovery
  const set = (patch: Partial<RecoveryDraft>) => setP({ recovery: { ...d, ...patch } })
  return (
    <Page title={t('rec.loc.title')}>
      <h2 className="font-semibold">{t('rec.loc.joint')}</h2>
      <div className="grid grid-cols-2 gap-3">
        {JOINTS.map((j) => <Choice key={j} title={t(`joint.${j}` as Key)} selected={d.joint === j} onClick={() => set({ joint: j, limits: {} })} />)}
      </div>
      <h2 className="font-semibold">{t('rec.loc.side')}</h2>
      <div className="grid grid-cols-2 gap-3">
        {(['left', 'right'] as const).map((s) => <Choice key={s} title={t(`side.${s}` as Key)} selected={d.side === s} onClick={() => set({ side: s })} />)}
      </div>
      <Button className="w-full" disabled={!d.joint || !d.side} onClick={() => nav('/recovery/details')}>{t('common.next')}</Button>
    </Page>
  )
}

export function RecoveryDetails() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, setP } = useApp()
  const d = p.recovery
  const [show, setShow] = useState(false)
  const err = useMemo(() => validateRecovery(d), [d])
  const set = (patch: Partial<RecoveryDraft>) => setP({ recovery: { ...d, ...patch } })
  if (!d.joint) { setTimeout(() => nav('/recovery/where', { replace: true })); return null }
  return (
    <Page title={t('rec.det.title')}>
      <div className="grid grid-cols-3 gap-2">
        {(['fracture', 'surgery', 'sprain'] as const).map((k) => <Choice key={k} title={t(`rec.type.${k}` as Key)} selected={d.injuryType === k} onClick={() => set({ injuryType: k })} />)}
      </div>
      <Field label={t('rec.det.date')} placeholder="YYYY-MM-DD" inputMode="numeric" value={d.injuryDate} onChange={(e) => set({ injuryDate: e.target.value })} error={show && err.injuryDate ? t(err.injuryDate) : undefined} />
      <Field label={`${t('rec.det.cast')} (${t('common.optional')})`} placeholder="YYYY-MM-DD" inputMode="numeric" value={d.castRemoved} onChange={(e) => set({ castRemoved: e.target.value })} error={show && err.castRemoved ? t(err.castRemoved) : undefined} />
      {MOVEMENTS_BY_JOINT[d.joint].map((m) => (
        <Field key={m} label={t('rec.det.limit', { movement: t(`movement.${m}` as Key) })} hint={t('rec.det.limit.hint')} inputMode="numeric" value={d.limits[m] ?? ''}
          onChange={(e) => set({ limits: { ...d.limits, [m]: e.target.value } })} error={show && err.limits?.[m] ? t(err.limits[m]) : undefined} />
      ))}
      <Field label={`${t('rec.det.weight')} (${t('common.optional')})`} value={d.weightBearing} onChange={(e) => set({ weightBearing: e.target.value })} />
      <PainScale label={t('rec.det.pain')} value={d.pain} onChange={(n) => set({ pain: n })} />
      <Button className="w-full" onClick={() => { setShow(true); if (recoveryValid(err)) nav('/recovery/wear') }}>{t('rec.det.next')}</Button>
    </Page>
  )
}

export function RecoveryWear() {
  const { t } = useT()
  const nav = useNavigate()
  const { p } = useApp()
  const j = p.recovery.joint
  if (!j) return null
  return (
    <Page title={t('rec.wear.title', { joint: t(`joint.${j}` as Key).toLowerCase() })} sub={t('rec.wear.note')}>
      <Card><h2 className="font-semibold">1. {t('rec.wear.main')}</h2><p className="text-muted">{t(`rec.wear.${j}.main` as Key)}</p></Card>
      <Card><h2 className="font-semibold">2. {t('rec.wear.small')}</h2><p className="text-muted">{t(`rec.wear.${j}.small` as Key)}</p></Card>
      <Button className="w-full" onClick={() => nav('/recovery/calibrate')}>{t('rec.wear.done')}</Button>
    </Page>
  )
}

type CalState = 'idle' | 'busy' | 'ok' | 'notsteady' | 'noband' | 'error'

/** Shared by setup and the home "Recalibrate" button. */
export function useCalibration(joint: Joint | null) {
  const { client } = useApp()
  const [state, setState] = useState<CalState>('idle')
  const run = useCallback(async () => {
    if (!client || !joint) return
    setState('busy')
    try {
      const r = await client.calibrate(joint)
      const res = r.result
      setState(res?.ok ? 'ok' : res?.error === 'no_band_data' ? 'noband' : 'notsteady')
    } catch { setState('error') }
  }, [client, joint])
  return { state, run }
}

export function Calibrate() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, client, setP } = useApp()
  const j = p.recovery.joint
  const { state, run } = useCalibration(j)
  const [err, setErr] = useState<string | null>(null)
  if (!j) return null
  const go = async () => {
    try {
      await client?.startRecovery(p.recovery.injuryType, toRecoveryCase(p.recovery))
      setP({ started: true, programType: 'recovery', purpose: 'recovery' })
      nav('/recovery/assess', { replace: true })
    } catch (e) { setErr(errMsg(e)) }
  }
  return (
    <Page title={t('rec.cal.title')} sub={t('rec.cal.body', { joint: t(`joint.${j}` as Key).toLowerCase() })}>
      {state === 'ok' && <Card><Badge level="ok">✓ {t('rec.cal.ok')}</Badge></Card>}
      {state === 'notsteady' && <p role="alert" className="text-crit">{t('rec.cal.notsteady')}</p>}
      {state === 'noband' && <p role="alert" className="text-crit">{t('rec.cal.noband')}</p>}
      {err && <p role="alert" className="text-crit">{err}</p>}
      {state !== 'ok'
        ? <Button className="w-full" disabled={state === 'busy'} onClick={() => void run()}>{state === 'busy' ? t('rec.cal.busy') : t('rec.cal.start')}</Button>
        : <Button className="w-full" onClick={() => void go()}>{t('common.next')}</Button>}
    </Page>
  )
}

/** Live range-of-movement test: start, move, stop. */
export function RomTest({ movement, phase, onResult }: { movement: string; phase: 'baseline' | 'pre' | 'post'; onResult: (r: RomResult) => void }) {
  const { t } = useT()
  const { client, live, p } = useApp()
  const [running, setRunning] = useState(false)
  const [res, setRes] = useState<RomResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const side = p.recovery.side ?? 'right'
  const start = async () => { setErr(null); setRes(null); try { await client?.romStart(movement, phase, side); setRunning(true) } catch (e) { setErr(errMsg(e)) } }
  const stop = async () => {
    try { const r = await client!.romStop(); setRunning(false); setRes(r); onResult(r) } catch (e) { setErr(errMsg(e)); setRunning(false) }
  }
  return (
    <Card className="space-y-3">
      <h3 className="font-semibold">{t(`movement.${movement}` as Key)}</h3>
      {running && <Gauge angle={live.angle?.angle ?? 0} max={NORMAL_ROM[movement] ?? 180} label={t(`movement.${movement}` as Key)} />}
      {err && <p role="alert" className="text-crit">{err}</p>}
      {res && (<div role="status"><p className="font-semibold">{t('rec.assess.rom.value', { rom: Math.round(res.rom), min: Math.round(res.min_angle), max: Math.round(res.max_angle), pct: Math.round(res.normal_pct) })}</p>
        {res.over_limit && <p className="text-warn">{t('rec.assess.limit', { limit: res.limit_angle ?? '' })}</p>}</div>)}
      {running ? <Button className="w-full" variant="danger" onClick={() => void stop()}>{t('rec.assess.stop')}</Button>
        : <Button className="w-full" variant={res ? 'secondary' : 'primary'} onClick={() => void start()}>{t('rec.assess.start')}</Button>}
    </Card>
  )
}

export function GripTest({ side, onDone }: { side: 'left' | 'right'; onDone?: () => void }) {
  const { t } = useT()
  const { client } = useApp()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const run = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await client!.gripTest(side)
      const g = r.result as { ok?: boolean; grip_peak?: number; grip_mean?: number } | null
      setMsg(g?.ok ? t('rec.assess.grip.result', { peak: g.grip_peak ?? 0, mean: g.grip_mean ?? 0 }) : t('rec.assess.grip.fail'))
      if (g?.ok) onDone?.()
    } catch (e) { setMsg(errMsg(e)) } finally { setBusy(false) }
  }
  return (
    <Card className="space-y-3"><h3 className="font-semibold">{t('rec.assess.grip')}</h3><p className="text-muted">{t('rec.assess.grip.body')}</p>
      {msg && <p role="status" className="font-semibold">{msg}</p>}
      <Button className="w-full" variant="secondary" disabled={busy} onClick={() => void run()}>{busy ? t('common.loading') : t('rec.assess.grip.start', { side: t(`side.${side}` as Key) })}</Button></Card>
  )
}

export function Assess() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, client } = useApp()
  const j = p.recovery.joint
  const [done, setDone] = useState<Record<string, boolean>>({})
  const [pain, setPain] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  if (!j) return null
  const mv = MOVEMENTS_BY_JOINT[j]
  const main = MAIN_MOVEMENT[j]
  const ready = !!done[main] && pain != null
  const make = async () => {
    setBusy(true); setErr(null)
    try { await client!.pain(pain!, 'baseline'); await client!.makePlan(); nav('/recovery/plan') } catch (e) { setErr(errMsg(e)) } finally { setBusy(false) }
  }
  return (
    <Page title={t('rec.assess.title')} sub={t('rec.assess.rom.body')} back={false}>
      {mv.map((m) => <RomTest key={m} movement={m} phase="baseline" onResult={() => setDone((d) => ({ ...d, [m]: true }))} />)}
      {ARM_JOINTS.includes(j) && <GripTest side={p.recovery.side ?? 'right'} />}
      <PainScale label={t('rec.assess.pain')} value={pain} onChange={setPain} />
      {err && <p role="alert" className="text-crit">{err}</p>}
      <Button className="w-full" disabled={!ready || busy} onClick={() => void make()}>{t('rec.assess.next')}</Button>
    </Page>
  )
}

export function exName(ex: ExerciseDef | undefined, lang: string): string { return ex ? (lang === 'hi' ? ex.name_hi : ex.name) : '' }

export function PlanView({ plan, lib, onApprove }: { plan: Plan; lib: Record<string, ExerciseDef>; onApprove?: (by: string) => void }) {
  const { t, lang } = useT()
  const [by, setBy] = useState('')
  return (
    <div className="space-y-4">
      <Card><div className="flex items-center justify-between"><h2 className="text-lg font-bold">{t('rec.plan.stage', { stage: plan.stage })}: {t(`rec.plan.stage.${plan.stage}` as Key)}</h2>
        {plan.approved_by ? <Badge level="ok">✓ {t('rec.plan.approved', { by: plan.approved_by })}</Badge> : <Badge level="warning">!</Badge>}</div>
        {!plan.approved_by && <p className="mt-2" role="note">{plan.status === 'may_start_stage1' ? t('rec.plan.stage1') : t('rec.plan.waiting')}</p>}
        {plan.notes.map((n) => <p key={n} className="mt-1 text-sm text-muted">{t(`rec.plan.note.${n}` as Key)}</p>)}</Card>
      <ul className="space-y-3">
        {plan.items.map((i) => (
          <li key={i.exercise_id}><Card><h3 className="font-semibold">{exName(lib[i.exercise_id], lang)}</h3>
            <p className="text-sm text-muted">{lib[i.exercise_id]?.how}</p>
            <p className="mt-1 text-sm">{t('rec.plan.items', { sets: i.sets, reps: i.reps, angle: Math.round(i.target_angle), hold: i.hold_s })}</p></Card></li>
        ))}
      </ul>
      {!plan.approved_by && onApprove && (
        <Card className="space-y-2"><Field label={t('rec.plan.approve.by')} value={by} onChange={(e) => setBy(e.target.value)} />
          <Button variant="secondary" className="w-full" disabled={by.trim().length < 2} onClick={() => onApprove(by.trim())}>{t('rec.plan.approve')}</Button></Card>)}
      <p className="text-xs text-muted">{t('rec.plan.disclaimer')}</p>
    </div>
  )
}

export function useLibrary(joint: Joint | null) {
  const { client } = useApp()
  const [lib, setLib] = useState<Record<string, ExerciseDef>>({})
  useEffect(() => { if (client && joint) void client.exercises(joint).then((l) => setLib(Object.fromEntries(l.map((e) => [e.id, e])))).catch(() => undefined) }, [client, joint])
  return lib
}

export function PlanScreen() {
  const { t } = useT()
  const nav = useNavigate()
  const { client, p } = useApp()
  const [plan, setPlan] = useState<Plan | null>(null)
  const lib = useLibrary(p.recovery.joint)
  useEffect(() => { void client?.plan().then(setPlan).catch(() => undefined) }, [client])
  if (!plan) return <Page title={t('rec.plan.title')} back={false}><p>{t('common.loading')}</p></Page>
  return (
    <Page title={t('rec.plan.title')} back={false}>
      <PlanView plan={plan} lib={lib} onApprove={(by) => void client?.approvePlan(by).then(setPlan)} />
      <Button className="w-full" onClick={() => nav('/rehab', { replace: true })}>{t('rec.plan.go')}</Button>
    </Page>
  )
}

export function RehabHome() {
  const { t, lang } = useT()
  const nav = useNavigate()
  const { client, p } = useApp()
  const j = p.recovery.joint
  const [plan, setPlan] = useState<Plan | null>(null)
  const lib = useLibrary(j)
  const { state: calState, run: recal } = useCalibration(j)
  const [rom, setRom] = useState(false)
  useEffect(() => { void client?.plan().then(setPlan).catch(() => setPlan(null)) }, [client])
  if (!j) return <Page title={t('rec.home.title')} back={false}><Button onClick={() => nav('/recovery/where')}>{t('common.next')}</Button></Page>
  const canStart = plan && (plan.approved_by || plan.status === 'may_start_stage1')
  return (
    <main className="mx-auto max-w-xl space-y-4 px-4 pb-24 pt-4">
      <h1 className="text-2xl font-bold">{t('rec.home.title')}</h1>
      <Card className="space-y-2"><p>{t('rec.home.needcal', { joint: t(`joint.${j}` as Key).toLowerCase() })}</p>
        <div className="flex items-center gap-3"><Button variant="secondary" onClick={() => void recal()} disabled={calState === 'busy'}>{calState === 'busy' ? t('rec.cal.busy') : t('rec.home.recal')}</Button>
          {calState === 'ok' && <Badge level="ok">✓ {t('rec.cal.ok')}</Badge>}{calState === 'notsteady' && <span className="text-crit">{t('rec.cal.notsteady')}</span>}</div></Card>
      <h2 className="font-semibold">{t('rec.home.today')}</h2>
      {!plan && <Card><p>{t('rec.home.noplan')}</p><Button className="mt-2" onClick={() => nav('/recovery/assess')}>{t('rec.assess.title')}</Button></Card>}
      {plan && !canStart && <Card><p role="note">{t('rec.plan.waiting')}</p></Card>}
      {plan?.items.map((i: PlanItem) => (
        <Card key={i.exercise_id} className="flex items-center justify-between gap-3">
          <div><h3 className="font-semibold">{exName(lib[i.exercise_id], lang)}</h3><p className="text-sm text-muted">{t('rec.plan.items', { sets: i.sets, reps: i.reps, angle: Math.round(i.target_angle), hold: i.hold_s })}</p></div>
          <Button disabled={!canStart} onClick={() => nav(`/rehab/session/${i.exercise_id}`)}>{t('rec.home.start')}</Button>
        </Card>
      ))}
      {plan && <PlanView plan={{ ...plan, items: [] }} lib={lib} onApprove={(by) => void client?.approvePlan(by).then(setPlan)} />}
      <div className="grid grid-cols-2 gap-3">
        <Button variant="secondary" onClick={() => setRom((x) => !x)}>{t('rec.home.reassess')}</Button>
        {ARM_JOINTS.includes(j) && <Button variant="secondary" onClick={() => nav('/rehab/grip')}>{t('rec.home.grip')}</Button>}
      </div>
      {rom && <RomTest movement={MAIN_MOVEMENT[j]} phase="pre" onResult={() => undefined} />}
    </main>
  )
}

export function GripScreen() {
  const { t } = useT()
  const { p } = useApp()
  return <Page title={t('rec.assess.grip')}><GripTest side={p.recovery.side ?? 'right'} /><GripTest side={p.recovery.side === 'left' ? 'right' : 'left'} /></Page>
}

export function Session() {
  const { t, lang } = useT()
  const nav = useNavigate()
  const { id } = useParams()
  const { client, live, p, resetRehab, now } = useApp()
  const lib = useLibrary(p.recovery.joint)
  const ex = id ? lib[id] : undefined
  const [plan, setPlan] = useState<Plan | null>(null)
  const [phase, setPhase] = useState<'pre' | 'run' | 'post'>('pre')
  const [pain, setPain] = useState<number | null>(null)
  const [postPain, setPostPain] = useState<number | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [result, setResult] = useState<SessionResult | null>(null)
  const [painNow, setPainNow] = useState(false)
  const inZoneSince = useRef<number | null>(null)
  const item = plan?.items.find((i) => i.exercise_id === id)
  useEffect(() => { void client?.plan().then(setPlan).catch(() => undefined) }, [client])
  useEffect(() => { if (live.session && phase === 'run') { setResult(live.session); setPhase('post') } }, [live.session, phase])

  const a = live.angle
  useEffect(() => { inZoneSince.current = a?.in_target ? (inZoneSince.current ?? Date.now()) : null }, [a?.in_target])
  if (!ex || !plan) return <Page title={t('rec.ex.pre')}><p>{t('common.loading')}</p></Page>
  const target = item?.target_angle ?? (ex.target_pct / 100) * (NORMAL_ROM[ex.movement] ?? 100)
  const sets = item?.sets ?? ex.sets, reps = item?.reps ?? ex.reps
  const [up, down] = item?.tempo_s ?? ex.tempo_s
  const begin = async () => {
    setErr(null); resetRehab()
    try { await client!.exStart(ex.id, item, pain ?? 0); setPhase('run') } catch (e) { setErr(e instanceof ApiError && e.message.includes('pain_too_high') ? t('rec.ex.pain.blocked') : errMsg(e)) }
  }
  const stop = async () => { try { const r = await client!.exStop(); setResult(r); setPhase('post') } catch { setPhase('post') } }
  const reportPain = async (n: number) => { setPainNow(false); await client?.exPain(n).catch(() => undefined) }
  const finish = async () => { if (postPain != null) await client?.pain(postPain, 'post').catch(() => undefined); resetRehab(); nav('/rehab', { replace: true }) }

  const repNo = a?.rep ?? 0
  const setNo = Math.min(sets, Math.floor(repNo / reps) + 1)
  const inSet = Math.min(reps, (repNo % reps) + 1)
  const lastRep = live.reps[live.reps.length - 1]
  const resting = !!lastRep && lastRep.rep_in_set === reps && lastRep.set < sets && now - (lastRep.ts ?? 0) < 30_000
  const holdLeft = a?.in_target && inZoneSince.current ? Math.max(0, Math.ceil(ex.hold_s - (now - inZoneSince.current) / 1000)) : null
  const over = !!a?.over_limit

  return (
    <Page title={exName(ex, lang)} sub={ex.how}>
      {phase === 'pre' && (<>
        <h2 className="font-semibold">{t('rec.ex.pre')}</h2>
        <PainScale label={t('rec.ex.pre.pain')} value={pain} onChange={setPain} />
        <p className="text-sm text-muted">{t('rec.ex.tempo', { up, down })} · {t('rec.ex.target', { target: Math.round(target) })}</p>
        {err && <p role="alert" className="text-crit">{err}</p>}
        <Button className="w-full" disabled={pain == null} onClick={() => void begin()}>{t('rec.ex.begin')}</Button></>)}
      {phase === 'run' && (<>
        <Gauge angle={a?.angle ?? 0} max={NORMAL_ROM[ex.movement] ?? 180} target={target} limit={item?.limit_angle} inTarget={a?.in_target} over={over} label={t(`movement.${ex.movement}` as Key)} />
        <div className="text-center" aria-live="polite">
          <p className="text-2xl font-bold">{t('rec.ex.rep', { rep: inSet, reps })} · {t('rec.ex.set', { set: setNo, sets })}</p>
          <p className="text-muted">{t('rec.ex.target', { target: Math.round(target) })} · {t('rec.ex.tempo', { up, down })}</p>
          {a?.in_target && !over && <p className="font-semibold text-ok">✓ {holdLeft ? t('rec.ex.hold', { s: holdLeft }) : t('rec.ex.inzone')}</p>}
          {a?.too_fast && <p role="alert" className="text-xl font-bold text-warn">! {t('rec.ex.toofast')}</p>}
          {over && <p role="alert" className="text-xl font-bold text-crit">▲ {t('rec.ex.over')}</p>}
          {resting && <p className="text-lg font-semibold">{t('rec.ex.rest', { s: Math.max(0, 30 - Math.floor((now - (lastRep?.ts ?? now)) / 1000)) })}</p>}
        </div>
        {painNow ? <PainScale label={t('rec.ex.painnow')} value={null} onChange={(n) => void reportPain(n)} /> : (
          <div className="grid grid-cols-2 gap-3"><Button variant="secondary" onClick={() => setPainNow(true)}>{t('rec.ex.painnow')}</Button><Button variant="danger" onClick={() => void stop()}>{t('rec.ex.stop')}</Button></div>)}</>)}
      {phase === 'post' && (<>
        <Card className="space-y-1"><h2 className="text-lg font-bold">{t('rec.ex.done')}</h2>
          {result && <><p>{t(`rec.ex.reason.${result.stopped_reason}` as Key)}</p>
            <p className="text-sm text-muted">{t('rec.ex.sum', { reps: result.reps, avg: Math.round(result.avg_peak_angle ?? 0), hit: Math.round(result.target_hit_pct ?? 0), fast: result.too_fast_count })}</p></>}</Card>
        <PainScale label={t('rec.ex.post.pain')} value={postPain} onChange={setPostPain} />
        <Button className="w-full" onClick={() => void finish()}>{t('rec.ex.finish')}</Button></>)}
    </Page>
  )
}

const fmt = (ts: number) => new Date(ts).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

export function ProgressScreen() {
  const { t } = useT()
  const { client } = useApp()
  const [pr, setPr] = useState<Progress | null>(null)
  useEffect(() => { void client?.progress().then(setPr).catch(() => undefined) }, [client])
  if (!pr?.case) return <main className="px-4 pt-4"><h1 className="text-2xl font-bold">{t('rec.prog.title')}</h1><p className="mt-3">{t('rec.prog.empty')}</p></main>
  const rows = (pr.rom ?? []).map((r) => ({ d: new Date(r.day).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }), rom: Math.round(r.rom) }))
  const fv = forecastView(pr.forecast, rows.length)
  const last = pr.rom?.[pr.rom.length - 1]
  const grip = [...(pr.grip?.injured ?? []).map((g) => ({ side: 'injured', ts: g.ts, v: g.peak })), ...(pr.grip?.healthy ?? []).map((g) => ({ side: 'healthy', ts: g.ts, v: g.peak }))]
  return (
    <main className="mx-auto max-w-xl space-y-4 px-4 pt-4">
      <h1 className="text-2xl font-bold">{t('rec.prog.title')}</h1>
      <Card><h2 className="mb-2 font-semibold">{t('rec.prog.rom')}</h2>
        {last && <p className="mb-2 text-3xl font-bold">{Math.round(last.rom)}° <span className="text-base font-medium text-muted">{t('rec.prog.pct', { pct: Math.round(last.pct) })}</span></p>}
        {rows.length ? (
          <div className="h-48" role="img" aria-label={t('rec.prog.rom')}><ResponsiveContainer><LineChart data={rows} margin={{ left: 0, right: 8, top: 8 }}>
            <CartesianGrid stroke="rgb(var(--line))" strokeDasharray="3 3" /><XAxis dataKey="d" tick={{ fontSize: 11, fill: 'rgb(var(--muted))' }} /><YAxis width={34} tick={{ fontSize: 11, fill: 'rgb(var(--muted))' }} /><Tooltip />
            {pr.target != null && <ReferenceLine y={pr.target} stroke="rgb(var(--ok))" strokeDasharray="5 4" label={{ value: t('rec.prog.target'), fontSize: 11, fill: 'rgb(var(--ok))' }} />}
            <Line dataKey="rom" stroke="rgb(var(--brand))" strokeWidth={2.5} dot isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
        ) : <p>{t('rec.prog.empty')}</p>}</Card>
      {pr.adherence != null && <Card><p>{t('rec.prog.adherence', { pct: Math.round(pr.adherence * 100) })}</p></Card>}
      {grip.length > 0 && <Card><h2 className="mb-1 font-semibold">{t('rec.prog.grip')}</h2><ul>{grip.sort((a, b) => a.ts - b.ts).map((g, i) => <li key={i} className="flex justify-between"><span>{fmt(g.ts)} · {t(`rec.prog.grip.${g.side}` as Key)}</span><b>{g.v}</b></li>)}</ul></Card>}
      <Card><h2 className="mb-1 font-semibold">{t('rec.prog.forecast')}</h2>
        {fv.kind === 'need' && <p>{t('rec.prog.forecast.need', { n: fv.more })}</p>}
        {fv.kind === 'plateau' && <p>{t('rec.prog.forecast.plateau')}</p>}
        {fv.kind === 'done' && <p>{t('rec.prog.forecast.done')}</p>}
        {fv.kind === 'date' && <p>{fv.lo && fv.hi ? t('rec.prog.forecast.date', { date: fmt(fv.est), lo: fmt(fv.lo), hi: fmt(fv.hi), n: fv.n }) : t('rec.prog.forecast.date1', { date: fmt(fv.est), n: fv.n })}</p>}
        <p className="mt-1 text-sm text-muted">{t('rec.prog.forecast.estimate')}</p></Card>
    </main>
  )
}
