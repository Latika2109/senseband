import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { parsePairText } from '../api/discovery'
import { Page } from '../components/Page'
import { ChestArt, ClipArt, StrapArt, WaistArt, BatteryArt } from '../components/illustrations'
import { QrScanner, qrSupported } from '../components/QrScanner'
import { Badge, Button, Card, Choice, Field } from '../components/ui'
import { useT, type Key } from '../i18n'
import { useApp } from '../state/AppContext'
import { evaluateChecks, requiredPass, nextRoute } from '../state/flow'
import { emptyProfile, isValid, validateProfile, type ProfileDraft } from '../state/profile'
import type { SignalCheck } from '../api/types'

export function useNextRoute() {
  const { p } = useApp()
  return nextRoute({ profileDone: p.profileDone, bandUrl: p.bandUrl, programActive: p.started, programType: p.programType,
    purpose: p.purpose, durationChosen: p.durationChosen, wearDone: p.wearDone })
}

export function Welcome() {
  const { t } = useT()
  const nav = useNavigate()
  const { p } = useApp()
  return (
    <Page title={t('welcome.title')} sub={t('welcome.body')} back={false} footer={<p className="text-xs text-muted">{t('disclaimer')}</p>}>
      <div className="flex justify-center py-6" aria-hidden><img src="favicon.svg" alt="" className="h-28 w-28" /></div>
      <Button className="w-full" onClick={() => nav('/profile')}>{t('welcome.start')}</Button>
      {p.bandUrl && <Button variant="secondary" className="w-full" onClick={() => nav('/')}>{t('welcome.have')}</Button>}
      <Button variant="ghost" className="w-full" onClick={() => nav('/cloud/login')}>{t('welcome.signin')}</Button>
    </Page>
  )
}

export function Profile() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, setP } = useApp()
  const [d, setD] = useState<ProfileDraft>(p.profile ?? emptyProfile())
  const [show, setShow] = useState(false)
  const err = useMemo(() => validateProfile(d), [d])
  const e = (k: Key | undefined) => (show && k ? t(k) : undefined)
  const set = <K extends keyof ProfileDraft>(k: K, v: ProfileDraft[K]) => setD((x) => ({ ...x, [k]: v }))
  const submit = () => {
    setShow(true)
    if (!isValid(err)) return
    setP({ profile: d, profileDone: true })
    nav('/pair')
  }
  return (
    <Page title={t('profile.title')} sub={t('profile.sub')} path="/profile">
      <Field label={t('profile.name')} value={d.name} onChange={(x) => set('name', x.target.value)} error={e(err.name)} autoComplete="name" />
      <div className="grid grid-cols-2 gap-3">
        <Field label={t('profile.age')} inputMode="numeric" value={d.age} onChange={(x) => set('age', x.target.value)} error={e(err.age)} />
        <div className="space-y-1">
          <label htmlFor="sex" className="block text-sm font-medium">{t('profile.sex')}</label>
          <select id="sex" value={d.sex} onChange={(x) => set('sex', x.target.value as ProfileDraft['sex'])} className="min-h-12 w-full rounded-xl border border-line bg-surface px-3">
            <option value="">–</option><option value="f">{t('profile.sex.f')}</option><option value="m">{t('profile.sex.m')}</option><option value="o">{t('profile.sex.o')}</option>
          </select>
        </div>
        <Field label={`${t('profile.height')} (${t('common.optional')})`} inputMode="decimal" value={d.height} onChange={(x) => set('height', x.target.value)} error={e(err.height)} />
        <Field label={`${t('profile.weight')} (${t('common.optional')})`} inputMode="decimal" value={d.weight} onChange={(x) => set('weight', x.target.value)} error={e(err.weight)} />
      </div>
      <Field label={t('profile.conditions')} hint={t('profile.conditions.hint')} value={d.conditions} onChange={(x) => set('conditions', x.target.value)} />
      <Field label={t('profile.medicines')} hint={t('profile.medicines.hint')} value={d.medicines} onChange={(x) => set('medicines', x.target.value)} />
      <label className="flex items-start gap-3 rounded-xl border border-line bg-surface p-3">
        <input type="checkbox" className="mt-1 h-5 w-5" checked={d.beta} onChange={(x) => set('beta', x.target.checked)} />
        <span><span className="block font-medium">{t('profile.beta')}</span><span className="text-sm text-muted">{t('profile.beta.hint')}</span></span>
      </label>
      <Field label={`${t('profile.restinghr')} (${t('common.optional')})`} inputMode="numeric" value={d.restingHr} onChange={(x) => set('restingHr', x.target.value)} error={e(err.restingHr)} />
      <fieldset className="space-y-3">
        <legend className="font-semibold">{t('profile.contacts')}</legend>
        <p className="text-sm text-muted">{t('profile.contacts.hint')}</p>
        {show && err.contacts && <p role="alert" className="text-sm text-crit">{t(err.contacts)}</p>}
        {d.contacts.map((c, i) => (
          <Card key={i} className="space-y-3">
            <Field label={t('profile.contact.name')} value={c.name} onChange={(x) => set('contacts', d.contacts.map((y, j) => (j === i ? { ...y, name: x.target.value } : y)))} />
            <Field label={t('profile.contact.phone')} inputMode="tel" autoComplete="tel" value={c.phone}
              onChange={(x) => set('contacts', d.contacts.map((y, j) => (j === i ? { ...y, phone: x.target.value } : y)))} error={e(err.contactPhones?.[i])} />
            {d.contacts.length > 1 && <Button variant="ghost" onClick={() => set('contacts', d.contacts.filter((_, j) => j !== i))}>{t('profile.contact.remove')}</Button>}
          </Card>
        ))}
        {d.contacts.length < 5 && <Button variant="secondary" onClick={() => set('contacts', [...d.contacts, { name: '', phone: '' }])}>{t('profile.contact.add')}</Button>}
      </fieldset>
      <Button className="w-full" onClick={submit}>{t('common.next')}</Button>
    </Page>
  )
}

export function Pair() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, connect, autoDiscover, pairInfo, live, pushProfile } = useApp()
  const [phase, setPhase] = useState<'searching' | 'notfound' | 'found'>(p.bandUrl && pairInfo ? 'found' : 'searching')
  const [manual, setManual] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [scan, setScan] = useState(false)
  const [scanFail, setScanFail] = useState(false)
  const [busy, setBusy] = useState(false)

  const tryUrl = useCallback(async (raw: string) => {
    const url = parsePairText(raw)
    if (!url) { setErr(t('pair.bad')); return }
    setBusy(true); setErr(null)
    try { await connect(url); setPhase('found'); setScan(false) } catch { setErr(t('pair.failed', { url })) } finally { setBusy(false) }
  }, [connect, t])

  useEffect(() => {
    let live = true
    void autoDiscover().then(async (f) => {
      if (!live) return
      if (f) { await connect(f.url).catch(() => undefined); setPhase('found') } else setPhase('notfound')
    })
    return () => { live = false }
    // run once on entering the screen
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const st = live.status
  const rows: [Key, boolean][] = [
    ['pair.check.main', live.conn === 'online'],
    ['pair.check.small', !!st?.band.connected],
    ['pair.check.sensors', !!st && st.sensors.imu],
  ]
  const next = async () => { await pushProfile().catch(() => undefined); nav('/program') }
  return (
    <Page title={t('pair.title')} sub={t('pair.sub')} path="/pair">
      {phase === 'searching' && <Card><p role="status" aria-live="polite">{t('pair.searching')}</p></Card>}
      {phase === 'found' && (
        <Card className="space-y-3">
          <p className="font-semibold">{t('pair.found', { name: pairInfo?.name ?? 'SenseBand' })}{pairInfo?.mode === 'sim' && <span className="ml-2"><Badge level="info">{t('pair.sim')}</Badge></span>}</p>
          <ul className="space-y-2">
            {rows.map(([k, ok]) => (
              <li key={k} className="flex items-center justify-between">
                <span>{t(k)}</span>{ok ? <Badge level="ok">✓ {t('pair.ok')}</Badge> : <Badge level="warning">{t('pair.missing')}</Badge>}
              </li>
            ))}
          </ul>
          <Button className="w-full" onClick={next} disabled={live.conn !== 'online'}>{t('common.next')}</Button>
        </Card>
      )}
      {phase !== 'found' && (
        <>
          {phase === 'notfound' && <p className="text-muted">{t('pair.notfound')}</p>}
          {qrSupported() && !scanFail ? (
            <>
              <Button variant="secondary" className="w-full" onClick={() => setScan((s) => !s)}>{t('pair.scan')}</Button>
              {scan && <QrScanner onText={(x) => void tryUrl(x)} onError={() => { setScan(false); setScanFail(true) }} />}
            </>
          ) : <p className="text-sm text-muted">{t('pair.scan.unsupported')}</p>}
          <Field label={t('pair.manual')} hint={t('pair.manual.hint')} value={manual} onChange={(x) => setManual(x.target.value)} error={err ?? undefined} inputMode="url" autoCapitalize="none" />
          <Button className="w-full" onClick={() => void tryUrl(manual)} disabled={busy || !manual.trim()}>{t('pair.connect')}</Button>
        </>
      )}
    </Page>
  )
}

export function Program() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, setP } = useApp()
  const pick = (type: 'monitoring' | 'recovery') => { setP({ programType: type }); nav(type === 'monitoring' ? '/purpose' : '/recovery/where') }
  return (
    <Page title={t('program.title')} path="/program">
      <Choice icon="❤️" title={t('program.monitor')} sub={t('program.monitor.sub')} selected={p.programType === 'monitoring'} onClick={() => pick('monitoring')} />
      <Choice icon="🩹" title={t('program.recovery')} sub={t('program.recovery.sub')} selected={p.programType === 'recovery'} onClick={() => pick('recovery')} />
    </Page>
  )
}

export const PURPOSES = ['elderly', 'discharge', 'athlete', 'general'] as const
export function Purpose() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, setP } = useApp()
  const icons = { elderly: '🧓', discharge: '🏥', athlete: '🏃', general: '✔️' }
  return (
    <Page title={t('purpose.title')} path="/purpose">
      {PURPOSES.map((k) => (
        <Choice key={k} icon={icons[k]} title={t(`purpose.${k}` as Key)} sub={t(`purpose.${k}.sub` as Key)} selected={p.purpose === k}
          onClick={() => { setP({ purpose: k }); nav('/duration') }} />
      ))}
    </Page>
  )
}

export function Duration() {
  const { t } = useT()
  const nav = useNavigate()
  const { setP } = useApp()
  const [custom, setCustom] = useState('')
  const [showCustom, setShowCustom] = useState(false)
  const choose = (h: number | null) => { setP({ durationH: h, durationChosen: true }); nav('/wear') }
  const n = Number(custom)
  return (
    <Page title={t('duration.title')} path="/duration">
      <Choice title={t('duration.24')} onClick={() => choose(24)} />
      <Choice title={t('duration.48')} onClick={() => choose(48)} />
      <Choice title={t('duration.168')} onClick={() => choose(168)} />
      <Choice title={t('duration.continuous')} onClick={() => choose(null)} />
      <Choice title={t('duration.custom')} selected={showCustom} onClick={() => setShowCustom(true)} />
      {showCustom && (
        <div className="flex items-end gap-3">
          <div className="flex-1"><Field label={t('duration.custom.hours')} inputMode="numeric" value={custom} onChange={(x) => setCustom(x.target.value)} /></div>
          <Button disabled={!(n >= 1 && n <= 24 * 90)} onClick={() => choose(Math.round(n))}>{t('common.next')}</Button>
        </div>
      )}
    </Page>
  )
}

export function WearGuide() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, setP } = useApp()
  const athlete = p.purpose === 'athlete'
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const steps: { id: string; art: JSX.Element; t: Key; b: Key; extra?: Key }[] = [
    { id: 'waist', art: <WaistArt />, t: 'wear.waist.t', b: 'wear.waist.b' },
    athlete ? { id: 'strap', art: <StrapArt />, t: 'wear.strap.t', b: 'wear.strap.b' } : { id: 'ecg', art: <ChestArt />, t: 'wear.ecg.t', b: 'wear.ecg.b' },
    { id: 'clip', art: <ClipArt />, t: 'wear.clip.t', b: 'wear.clip.b', extra: athlete ? 'wear.clip.optional' : undefined },
    { id: 'battery', art: <BatteryArt />, t: 'wear.battery.t', b: 'wear.battery.b' },
  ]
  const all = steps.every((s) => checked[s.id] || (athlete && s.id === 'clip'))
  return (
    <Page title={t('wear.title')} sub={t('wear.sub')} path="/wear">
      {steps.map((s) => (
        <Card key={s.id} className="flex items-center gap-4">
          <div aria-hidden>{s.art}</div>
          <div className="flex-1">
            <h2 className="font-semibold">{t(s.t)}</h2><p className="text-sm text-muted">{t(s.b)}</p>
            {s.extra && <p className="mt-1 text-sm text-muted">{t(s.extra)}</p>}
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-5 w-5" checked={!!checked[s.id]} onChange={(e) => setChecked({ ...checked, [s.id]: e.target.checked })} />
              {t('common.done')}
            </label>
          </div>
        </Card>
      ))}
      <Button className="w-full" disabled={!all} onClick={() => { setP({ wearDone: true }); nav('/check') }}>{t('wear.done')}</Button>
    </Page>
  )
}

export function SignalCheckScreen() {
  const { t } = useT()
  const nav = useNavigate()
  const { p, setP, client, live } = useApp()
  const [sc, setSc] = useState<SignalCheck | null>(null)
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!client) return
    let alive = true
    const tick = () => client.signalCheck().then((x) => alive && setSc(x)).catch(() => undefined)
    void tick()
    const i = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(i) }
  }, [client])

  const items = evaluateChecks(sc, live.status, p.purpose)
  const ok = requiredPass(items)
  const tipFor = (id: string): string | undefined => {
    if (!sc) return undefined
    if (id === 'ecg') return sc.ecg.quality === 'good' ? undefined : sc.ecg.tip
    if (id === 'ppg') return sc.ppg.quality === 'good' ? undefined : sc.ppg.tip
    if (id === 'imu') return sc.imu.quality === 'good' ? undefined : sc.imu.tip
    return undefined
  }
  const detail = (id: string): string => {
    if (id === 'ecg' && sc) return t(`check.q.${sc.ecg.quality}` as Key)
    if (id === 'ppg' && sc) return t(`check.q.${sc.ppg.quality}` as Key)
    if (id === 'battery') return live.status?.band.battery_pct != null ? `${Math.round(live.status.band.battery_pct)} %` : ''
    if (id === 'internet') return t(live.status?.internet == null ? 'check.internet.unknown' : live.status.internet ? 'check.internet.yes' : 'check.internet.no')
    return ''
  }
  const start = async () => {
    if (!client) return
    setBusy(true); setErr(null)
    try {
      await client.startProgram('monitoring', p.purpose ?? 'general', p.durationH)
      setP({ started: true })
      nav('/live', { replace: true })
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }
  const badge = (s: string) => s === 'pass' ? <Badge level="ok">✓ {t('check.pass')}</Badge> : s === 'fail' ? <Badge level="warning">! {t('check.fail')}</Badge>
    : s === 'skipped' ? <Badge level="info">{t('check.skipped')}</Badge> : s === 'info' ? null : <Badge level="info">{t('check.wait')}</Badge>
  return (
    <Page title={t('check.title')} sub={t('check.sub')} path="/check">
      <ul className="space-y-3">
        {items.map((i) => (
          <li key={i.id}><Card>
            <div className="flex items-center justify-between gap-2"><span className="font-semibold">{t(`check.${i.id}` as Key)}</span>{badge(i.state)}</div>
            {(detail(i.id) || tipFor(i.id)) && <p className="mt-1 text-sm text-muted">{detail(i.id)}{tipFor(i.id) && i.state !== 'skipped' ? ` — ${tipFor(i.id)}` : ''}</p>}
          </Card></li>
        ))}
      </ul>
      {err && <p role="alert" className="text-crit">{err}</p>}
      {ok ? <Button className="w-full" onClick={start} disabled={busy}>{t('check.start')}</Button> : (
        <div className="space-y-3">
          <label className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-0.5 h-5 w-5" checked={ack} onChange={(e) => setAck(e.target.checked)} />{t('check.anyway.ack')}</label>
          <Button variant="secondary" className="w-full" disabled={!ack || busy} onClick={start}>{t('check.anyway')}</Button>
        </div>
      )}
    </Page>
  )
}
