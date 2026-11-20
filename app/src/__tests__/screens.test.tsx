import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'

const INFO = { name: 'SenseBand', api: 1, port: 8080, mode: 'sim', patient_id: 'p', urls: [], qr: '' }
const STATUS = { time: 0, mode: 'sim', uptime_s: 1, data_time_s: 10, sensors: { ecg: 'good', ppg: 'good', imu: true, temp: true }, band: { connected: true, battery_pct: 90, last_seen: 1, finger: true }, source: {}, sms: { modem: 'mock', ok: true }, internet: null, sync_backlog: 0, program: null, alerts: { countdown: [], recent: [] }, buzzer: false, rss_mb: 1, errors: 0, learning_since_ts: null }
const SC = { ecg: { quality: 'good', tip: '' }, ppg: { quality: 'good', tip: '' }, imu: { quality: 'good', activity: 'sitting', tip: '' }, temp: { quality: 'good', tip: '' }, ready: true }

class FakeWS {
  static last: FakeWS | null = null
  onopen: (() => void) | null = null; onclose: (() => void) | null = null; onmessage: ((e: { data: string }) => void) | null = null
  constructor() { FakeWS.last = this; setTimeout(() => { this.onopen?.(); this.onmessage?.({ data: JSON.stringify({ t: 'status', ...STATUS }) }) }, 0) }
  close() { /* noop */ }
}

const calls: { url: string; method: string; body?: string }[] = []
beforeEach(() => {
  localStorage.clear(); location.hash = ''; calls.length = 0
  vi.stubGlobal('WebSocket', FakeWS)
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(String(url)).pathname
    calls.push({ url: path, method: init?.method ?? 'GET', body: init?.body as string | undefined })
    const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status })
    if (path === '/api/pair') return json(INFO)
    if (path === '/api/signal-check') return json(SC)
    if (path === '/api/status') return json(STATUS)
    if (path === '/api/patient') return json({ id: 'p', emergency_contacts: [] })
    if (path === '/api/programs') return json({ id: 'prog1', type: 'monitoring', purpose: 'general', start_ts: 1, planned_end_ts: null, status: 'active' }, 201)
    return json({})
  }))
})
afterEach(() => vi.unstubAllGlobals())

describe('first launch to live dashboard', () => {
  it('a new user completes setup in a handful of taps and lands on the live screen', async () => {
    const u = userEvent.setup()
    render(<App />)
    await u.click(await screen.findByRole('button', { name: /get started/i }))
    // profile: only name, age and one contact are required
    await u.type(screen.getByLabelText(/full name/i), 'Asha Rao')
    await u.type(screen.getByLabelText(/^age/i), '67')
    await u.type(screen.getByLabelText(/mobile number/i), '9876543210')
    await u.click(screen.getByRole('button', { name: /^next$/i }))
    // pair: auto-discovery finds the (simulated) band
    expect(await screen.findByText(/found SenseBand/i)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /^next$/i })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /^next$/i }))
    await u.click(await screen.findByRole('button', { name: /health monitoring/i }))
    await u.click(await screen.findByRole('button', { name: /general check/i }))
    await u.click(await screen.findByRole('button', { name: /24 hours/i }))
    // wear guide: tick each step
    for (const box of await screen.findAllByRole('checkbox')) await u.click(box)
    await u.click(screen.getByRole('button', { name: /i have put it on/i }))
    // signal check passes -> start
    const start = await screen.findByRole('button', { name: /start monitoring/i }, { timeout: 4000 })
    await u.click(start)
    expect(await screen.findByRole('heading', { name: /^live$/i })).toBeInTheDocument()
    // the band got the profile and the program
    const put = calls.find((c) => c.url === '/api/patient' && c.method === 'PUT')
    expect(JSON.parse(put!.body!)).toMatchObject({ name: 'Asha Rao', emergency_contacts: [{ phone: '+919876543210' }] })
    expect(JSON.parse(calls.find((c) => c.url === '/api/programs')!.body!)).toEqual({ type: 'monitoring', purpose: 'general', duration_h: 24 })
  }, 20000)

  it('profile validation blocks progress and explains why', async () => {
    const u = userEvent.setup()
    render(<App />)
    await u.click(await screen.findByRole('button', { name: /get started/i }))
    await u.click(screen.getByRole('button', { name: /^next$/i }))
    expect(await screen.findByText(/please enter a name/i)).toBeInTheDocument()
    expect(screen.getByText(/add at least one emergency contact/i)).toBeInTheDocument()
  })

  it('switching to Hindi translates the screen', async () => {
    const u = userEvent.setup()
    render(<App />)
    await u.selectOptions(await screen.findByRole('combobox'), 'hi')
    expect(await screen.findByRole('button', { name: 'शुरू करें' })).toBeInTheDocument()
  })

  it('shows the countdown banner with a working I\'m OK button', async () => {
    localStorage.setItem('sb.state', JSON.stringify({ profileDone: true, bandUrl: 'http://band:8080', started: true, programType: 'monitoring', profile: { name: 'Asha', age: '67', contacts: [], sex: '', height: '', weight: '', conditions: '', medicines: '', beta: false, restingHr: '' } }))
    render(<App />)
    await screen.findByRole('heading', { name: /^live$/i })
    FakeWS.last!.onmessage?.({ data: JSON.stringify({ t: 'event', why: 'raised', id: 'a1', level: 'critical', kind: 'fall', message: 'Possible fall - no movement detected.', raised_t: 1, status: 'active', sms_sent: 0, countdown_ends: 31, countdown_left_s: 30, acknowledged_by: null }) })
    const btn = await screen.findByRole('button', { name: /i.m ok/i })
    expect(screen.getByText(/possible fall/i)).toBeInTheDocument()
    await userEvent.setup().click(btn)
    expect(calls.some((c) => c.url === '/api/imok' && c.method === 'POST')).toBe(true)
    FakeWS.last!.onmessage?.({ data: JSON.stringify({ t: 'event', why: 'cancelled', id: 'a1', level: 'critical', kind: 'fall', message: 'm', raised_t: 1, status: 'cancelled', sms_sent: 0, countdown_ends: 31, countdown_left_s: null, acknowledged_by: 'Asha' }) })
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
  })
})
