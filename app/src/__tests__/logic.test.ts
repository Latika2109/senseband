import { describe, expect, it, vi } from 'vitest'
import { BandClient } from '../api/client'
import { defaultCandidates, discover, parsePairText } from '../api/discovery'
import { activeCountdown, countdownLeft, initialLive, LiveConnection, liveReducer, type Socket } from '../api/live'
import type { AlertInfo, Vitals } from '../api/types'
import { detectLang, makeT } from '../i18n'
import { en } from '../i18n/en'
import { hi } from '../i18n/hi'
import { evaluateChecks, nextRoute, requiredPass, type FlowState } from '../state/flow'
import { emptyProfile, isValid, normalizePhone, toPatientPayload, validateProfile } from '../state/profile'

describe('i18n', () => {
  it('Hindi has exactly the English keys and no empty strings', () => {
    expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort())
    for (const [k, v] of Object.entries(hi)) expect(v.trim(), k).not.toBe('')
  })
  it('placeholders match between languages', () => {
    const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join()
    for (const k of Object.keys(en) as (keyof typeof en)[]) expect(ph(hi[k]), k).toBe(ph(en[k]))
  })
  it('interpolates and detects language', () => {
    expect(makeT('en')('common.step', { n: 2, total: 7 })).toBe('Step 2 of 7')
    expect(makeT('hi')('common.step', { n: 2, total: 7 })).toContain('2')
    expect(detectLang(null, 'hi-IN')).toBe('hi')
    expect(detectLang('en', 'hi-IN')).toBe('en')
    expect(detectLang(null, 'fr')).toBe('en')
  })
  it('never diagnoses', () => {
    for (const s of [en['rhythm.possible_irregular'], hi['rhythm.possible_irregular']]) expect(s.toLowerCase()).not.toMatch(/you have|atrial fibrillation|एएफ/)
    expect(en['disclaimer']).toMatch(/not a medical device/)
  })
})

describe('pairing', () => {
  it('parses QR text and typed addresses', () => {
    expect(parsePairText('senseband://192.168.1.20:8080')).toBe('http://192.168.1.20:8080')
    expect(parsePairText('192.168.4.1')).toBe('http://192.168.4.1:8080')
    expect(parsePairText('http://senseband.local')).toBe('http://senseband.local:8080')
    expect(parsePairText(' 10.0.0.5:9000 ')).toBe('http://10.0.0.5:9000')
  })
  it('rejects junk', () => {
    for (const bad of ['', 'hello world', 'javascript:alert(1)', 'ftp://x', '999.1.1.1', 'a'.repeat(300), 'http://']) expect(parsePairText(bad), bad).toBeNull()
  })
  it('discover returns the first band that answers and null when none do', async () => {
    const info = { name: 'SenseBand', api: 1, port: 8080, mode: 'sim', patient_id: 'p', urls: [], qr: '' }
    const f = vi.fn(async (url: string) => {
      if (String(url).startsWith('http://good')) return new Response(JSON.stringify(info), { status: 200 })
      throw new Error('unreachable')
    }) as unknown as typeof fetch
    const hit = await discover(['http://bad:8080', 'http://good:8080'], f)
    expect(hit?.url).toBe('http://good:8080')
    expect(await discover(['http://bad:8080'], f)).toBeNull()
    expect(await discover([], f)).toBeNull()
  })
  it('candidate order: same origin first, then saved, then mDNS/hotspot', () => {
    const c = defaultCandidates('http://1.2.3.4:8080', 'http://band:8080')
    expect(c[0]).toBe('http://band:8080'); expect(c[1]).toBe('http://1.2.3.4:8080'); expect(c.length).toBe(4)
  })
  it('client maps http errors to ApiError with detail', async () => {
    const f = (async () => new Response(JSON.stringify({ detail: 'nope' }), { status: 422 })) as unknown as typeof fetch
    await expect(new BandClient('http://x:1/', f).status()).rejects.toMatchObject({ status: 422, message: '"nope"' })
    expect(new BandClient('http://x:1//').baseUrl).toBe('http://x:1')
    expect(new BandClient('http://x:1').wsUrl()).toBe('ws://x:1/ws/live')
    expect(new BandClient('https://x').wsUrl()).toBe('wss://x/ws/live')
  })
})

describe('profile', () => {
  const good = () => ({ ...emptyProfile(), name: 'Asha Rao', age: '67', contacts: [{ name: 'Son', phone: '98765 43210' }] })
  it('normalises phone numbers', () => {
    expect(normalizePhone('98765 43210')).toBe('+919876543210')
    expect(normalizePhone('09876543210')).toBe('+919876543210')
    expect(normalizePhone('+44 7700 900123')).toBe('+447700900123')
    for (const bad of ['12345', '5876543210', 'abc', '']) expect(normalizePhone(bad), bad).toBeNull()
  })
  it('requires name, age and at least one valid contact', () => {
    expect(isValid(validateProfile(good()))).toBe(true)
    expect(validateProfile(emptyProfile())).toMatchObject({ name: 'err.name', age: 'err.age', contacts: 'err.contact' })
    expect(validateProfile({ ...good(), age: '0' }).age).toBe('err.age')
    expect(validateProfile({ ...good(), age: '121' }).age).toBe('err.age')
    expect(validateProfile({ ...good(), contacts: [{ name: 'x', phone: '123' }] }).contactPhones?.[0]).toBe('err.phone')
    expect(validateProfile({ ...good(), height: '9' }).height).toBe('err.number')
  })
  it('builds the band payload (beta blocker adds a medicine, age becomes dob)', () => {
    const p = toPatientPayload({ ...good(), beta: true, medicines: 'Metformin, Aspirin', conditions: 'diabetes', height: '160', sex: 'f', restingHr: '64' }, new Date('2026-11-20'))
    expect(p).toMatchObject({ name: 'Asha Rao', dob: '1959-01-01', sex: 'f', height_cm: 160, resting_hr: 64, conditions: ['diabetes'] })
    expect(p.medications).toEqual(['Metformin', 'Aspirin', 'beta-blocker'])
    expect(p.emergency_contacts).toEqual([{ name: 'Son', phone: '+919876543210', relation: '' }])
  })
})

describe('flow', () => {
  const base: FlowState = { profileDone: false, bandUrl: null, programActive: false, programType: null, purpose: null, durationChosen: false, wearDone: false }
  it('resumes at the first incomplete step', () => {
    expect(nextRoute(base)).toBe('/welcome')
    expect(nextRoute({ ...base, profileDone: true })).toBe('/pair')
    expect(nextRoute({ ...base, profileDone: true, bandUrl: 'x' })).toBe('/program')
    expect(nextRoute({ ...base, profileDone: true, bandUrl: 'x', programType: 'monitoring' })).toBe('/purpose')
    expect(nextRoute({ ...base, profileDone: true, bandUrl: 'x', programType: 'monitoring', purpose: 'general' })).toBe('/duration')
    expect(nextRoute({ ...base, profileDone: true, bandUrl: 'x', programType: 'monitoring', purpose: 'general', durationChosen: true })).toBe('/wear')
    expect(nextRoute({ ...base, profileDone: true, bandUrl: 'x', programType: 'monitoring', purpose: 'general', durationChosen: true, wearDone: true })).toBe('/check')
    expect(nextRoute({ ...base, profileDone: true, bandUrl: 'x', programActive: true })).toBe('/live')
  })
  it('signal check gates on ECG, clip (except athletes), IMU and SMS', () => {
    const sc = { ecg: { quality: 'good', tip: '' }, ppg: { quality: 'finger_off', tip: '' }, imu: { quality: 'good', activity: 'sitting', tip: '' }, temp: { quality: 'good', tip: '' }, ready: false } as never
    const st = { band: { battery_pct: 80 }, sms: { ok: true }, internet: null } as never
    expect(requiredPass(evaluateChecks(sc, st, 'general'))).toBe(false)
    expect(requiredPass(evaluateChecks(sc, st, 'athlete'))).toBe(true)
    expect(requiredPass(evaluateChecks(sc, { ...(st as object), sms: { ok: false } } as never, 'athlete'))).toBe(false)
    expect(requiredPass(evaluateChecks(null, null, 'general'))).toBe(false)
    expect(evaluateChecks(sc, { ...(st as object), band: { battery_pct: 10 } } as never, 'athlete').find((i) => i.id === 'battery')?.state).toBe('fail')
  })
})

const vit = (o: Partial<Vitals> = {}): Vitals => ({ t: 'vitals', ts: 1, hr: 72, hr_src: 'ecg', hr_expected: 70, hr_range: [52, 95], context: 'sitting', posture: 'sitting', intensity: 0, spo2: 97, temp: 36.5, rhythm: 'regular', quality: { ecg: 'good', ppg: 'good' }, ews: 0, status: 'normal', ...o })
const alert = (o: Partial<AlertInfo> = {}): AlertInfo => ({ id: 'a1', level: 'critical', kind: 'fall', message: 'm', raised_t: 5, status: 'active', sms_sent: 0, countdown_ends: 35, countdown_left_s: 30, acknowledged_by: null, ...o })

describe('live store', () => {
  it('keeps a bounded vitals history and a 6 s ECG window', () => {
    let s = initialLive
    for (let i = 0; i < 700; i++) s = liveReducer(s, { type: 'msg', msg: vit({ ts: i }), now: 0 })
    expect(s.history.length).toBe(600); expect(s.history[0]?.ts).toBe(100)
    for (let i = 0; i < 60; i++) s = liveReducer(s, { type: 'msg', msg: { t: 'ecg', ts: i, fs: 250, samples: new Array(50).fill(1) }, now: 0 })
    expect(s.ecg.length).toBe(1500)
  })
  it('tracks the I\'m-OK countdown from the moment it was received and clears on cancel', () => {
    let s = liveReducer(initialLive, { type: 'msg', msg: { t: 'event', why: 'raised', ...alert() }, now: 1000 })
    const a = activeCountdown(s)!
    expect(countdownLeft(a, 1000)).toBe(30); expect(countdownLeft(a, 11000)).toBe(20); expect(countdownLeft(a, 99000)).toBe(0)
    s = liveReducer(s, { type: 'msg', msg: { t: 'event', why: 'cancelled', ...alert({ status: 'cancelled', countdown_left_s: null }) }, now: 5000 })
    expect(activeCountdown(s)).toBeNull(); expect(Object.keys(s.alerts)).toEqual(['a1'])
  })
  it('ignores pings and keeps conn state', () => {
    let s = liveReducer(initialLive, { type: 'conn', conn: 'online' })
    s = liveReducer(s, { type: 'msg', msg: { t: 'ping' }, now: 0 })
    expect(s.conn).toBe('online')
  })
  it('LiveConnection reconnects with backoff and stops cleanly', () => {
    vi.useFakeTimers()
    const sockets: (Socket & { closeNow: () => void })[] = []
    const conns: string[] = []; const msgs: unknown[] = []
    const lc = new LiveConnection('ws://x', { msg: (m) => msgs.push(m), conn: (c) => conns.push(c) }, () => {
      const s = { onopen: null, onclose: null, onmessage: null, close() { /* noop */ }, closeNow() { s.onclose?.() } } as Socket & { closeNow: () => void }
      sockets.push(s); return s
    })
    lc.start(); sockets[0]!.onopen?.(); sockets[0]!.onmessage?.({ data: '{"t":"ping"}' }); sockets[0]!.onmessage?.({ data: 'not json' })
    expect(conns).toEqual(['connecting', 'online']); expect(msgs).toHaveLength(1)
    sockets[0]!.closeNow(); vi.advanceTimersByTime(600)
    expect(sockets).toHaveLength(2); expect(conns.at(-1)).toBe('connecting')
    lc.stop(); sockets[1]!.closeNow(); vi.advanceTimersByTime(20000)
    expect(sockets).toHaveLength(2)
    vi.useRealTimers()
  })
})
