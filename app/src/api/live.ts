import type { AlertInfo, AngleMsg, EcgMsg, EventMsg, LiveMsg, RepMsg, SessionResult, Status, Vitals } from './types'

export const ECG_SECONDS = 6
export const HISTORY_POINTS = 600

export interface LiveState {
  conn: 'connecting' | 'online' | 'offline'
  vitals: Vitals | null
  status: Status | null
  ecg: number[]; ecgFs: number
  history: { ts: number; hr: number | null; expected: number }[]
  alerts: Record<string, AlertInfo & { rxAt: number }>
  angle: AngleMsg | null; angleTrace: number[]; reps: RepMsg[]; limitExceeded: number | null; session: SessionResult | null
}

export const initialLive: LiveState = { conn: 'connecting', vitals: null, status: null, ecg: [], ecgFs: 250, history: [], alerts: {},
  angle: null, angleTrace: [], reps: [], limitExceeded: null, session: null }

export type LiveAction = { type: 'msg'; msg: LiveMsg; now: number } | { type: 'conn'; conn: LiveState['conn'] } | { type: 'reset' } | { type: 'rehab_reset' }

function addAlert(alerts: LiveState['alerts'], a: AlertInfo, now: number) {
  return { ...alerts, [a.id]: { ...a, rxAt: now } }
}

export function liveReducer(s: LiveState, a: LiveAction): LiveState {
  if (a.type === 'reset') return initialLive
  if (a.type === 'conn') return { ...s, conn: a.conn }
  if (a.type === 'rehab_reset') return { ...s, angle: null, angleTrace: [], reps: [], limitExceeded: null, session: null }
  const m = a.msg
  switch (m.t) {
    case 'vitals': {
      const history = [...s.history, { ts: m.ts, hr: m.hr, expected: m.hr_expected }].slice(-HISTORY_POINTS)
      return { ...s, vitals: m, history }
    }
    case 'ecg': {
      const e = m as EcgMsg
      const keep = Math.round(e.fs * ECG_SECONDS)
      return { ...s, ecgFs: e.fs, ecg: [...s.ecg, ...e.samples].slice(-keep) }
    }
    case 'event': {
      const { t: _t, why: _w, ...alert } = m as EventMsg
      void _t; void _w
      return { ...s, alerts: addAlert(s.alerts, alert, a.now) }
    }
    case 'status': {
      const { t: _t, ...st } = m
      void _t
      let alerts = s.alerts
      for (const c of st.alerts.countdown) alerts = addAlert(alerts, c, a.now)
      for (const r of st.alerts.recent) if (!alerts[r.id]) alerts = addAlert(alerts, r, a.now)
      return { ...s, status: st as Status, alerts }
    }
    case 'angle': {
      const g = m as AngleMsg
      return { ...s, angle: g, angleTrace: [...s.angleTrace, g.angle].slice(-200) }
    }
    case 'rep':
      return { ...s, reps: [...s.reps, m as RepMsg] }
    case 'limit_exceeded':
      return { ...s, limitExceeded: a.now }
    case 'exercise_done': {
      const { t: _t, ...res } = m
      void _t
      return { ...s, session: res as SessionResult }
    }
    default:
      return s
  }
}

/** Seconds left of the "I'm OK" window for an active critical alert, ticking from when we received it. */
export function countdownLeft(a: AlertInfo & { rxAt: number }, now: number): number | null {
  if (a.status !== 'active' || a.countdown_left_s == null) return null
  return Math.max(0, Math.ceil(a.countdown_left_s - (now - a.rxAt) / 1000))
}

export function activeCountdown(s: LiveState): (AlertInfo & { rxAt: number }) | null {
  const act = Object.values(s.alerts).filter((x) => x.status === 'active' && x.countdown_left_s != null)
  return act.sort((x, y) => y.raised_t - x.raised_t)[0] ?? null
}

export interface Socket { onopen: (() => void) | null; onclose: (() => void) | null; onmessage: ((e: { data: string }) => void) | null; close(): void }

/** WebSocket with automatic reconnect (the band may reboot or the phone may change Wi-Fi). */
export class LiveConnection {
  private ws: Socket | null = null
  private timer: ReturnType<typeof setTimeout> | undefined
  private stopped = false
  private attempt = 0
  constructor(
    private url: string,
    private on: { msg: (m: LiveMsg) => void; conn: (c: LiveState['conn']) => void },
    private make: (url: string) => Socket = (u) => new WebSocket(u) as unknown as Socket,
  ) {}

  start() { this.stopped = false; this.open() }
  stop() { this.stopped = true; clearTimeout(this.timer); this.ws?.close(); this.ws = null }

  private open() {
    this.on.conn('connecting')
    const ws = this.make(this.url)
    this.ws = ws
    ws.onopen = () => { this.attempt = 0; this.on.conn('online') }
    ws.onmessage = (e) => {
      try { this.on.msg(JSON.parse(e.data) as LiveMsg) } catch { /* ignore malformed frame */ }
    }
    ws.onclose = () => {
      if (this.stopped) return
      this.on.conn('offline')
      const delay = Math.min(10000, 500 * 2 ** this.attempt++)
      this.timer = setTimeout(() => this.open(), delay)
    }
  }
}
