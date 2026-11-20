import type { AlertInfo, EventRow, ExerciseDef, Joint, PairInfo, PatientRow, Plan, PlanItem, Program, Progress, RehabState, RomResult, SessionResult, SignalCheck, Status } from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export type Fetch = typeof fetch

export class BandClient {
  constructor(public baseUrl: string, private fetchImpl: Fetch = (...a) => fetch(...a)) {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
  }

  private async req<T>(method: string, path: string, body?: unknown, timeoutMs = 8000): Promise<T> {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), timeoutMs)
    try {
      const r = await this.fetchImpl(this.baseUrl + path, {
        method, signal: ctl.signal,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!r.ok) {
        let detail = r.statusText
        try { detail = JSON.stringify((await r.json()).detail ?? detail) } catch { /* not json */ }
        throw new ApiError(r.status, detail)
      }
      return (await r.json()) as T
    } finally { clearTimeout(timer) }
  }

  pair = () => this.req<PairInfo>('GET', '/api/pair', undefined, 2500)
  status = () => this.req<Status>('GET', '/api/status')
  signalCheck = () => this.req<SignalCheck>('GET', '/api/signal-check')
  patient = () => this.req<PatientRow>('GET', '/api/patient')
  putPatient = (p: Partial<PatientRow>) => this.req<PatientRow>('PUT', '/api/patient', p)
  startProgram = (type: string, purpose: string, duration_h: number | null) =>
    this.req<Program>('POST', '/api/programs', { type, purpose, duration_h })
  stopProgram = (id: string) => this.req<Program>('POST', `/api/programs/${id}/stop`)
  events = (limit = 100) =>
    this.req<{ rows: EventRow[] }>('GET', `/api/history?kind=events&limit=${limit}`).then((r) => r.rows.reverse())
  ack = (id: string, by: string) => this.req<EventRow>('POST', `/api/events/${id}/ack`, { by })
  imOk = (by: string) => this.req<{ cancelled: AlertInfo[] }>('POST', '/api/imok', { by })
  sos = () => this.req<{ alert: AlertInfo | null }>('POST', '/api/sos')
  strip = (id: string) => this.req<{ fs: number; samples: number[] }>('GET', `/api/events/${id}/strip`)
  startRecovery = (purpose: string, recovery_case: Record<string, unknown>) =>
    this.req<Program>('POST', '/api/programs', { type: 'recovery', purpose, duration_h: null, recovery_case })
  rehabState = () => this.req<RehabState>('GET', '/api/rehab/state')
  calibrate = (joint: Joint) => this.req<RehabState>('POST', '/api/calibrate', { joint, wait: true }, 30000)
  romStart = (movement: string, phase: string, side: string) => this.req<RehabState>('POST', '/api/assessments/rom/start', { movement, phase, side })
  romStop = () => this.req<RomResult>('POST', '/api/assessments/rom/stop')
  gripTest = (side: string) => this.req<RehabState>('POST', '/api/assessments/grip', { side, wait: true }, 30000)
  pain = (score: number, phase: string) => this.req<{ stopped_exercise: boolean }>('POST', '/api/assessments/pain', { score, phase })
  exercises = (joint: string) => this.req<ExerciseDef[]>('GET', `/api/exercises?joint=${joint}`)
  plan = () => this.req<Plan>('GET', '/api/plan')
  makePlan = () => this.req<Plan>('POST', '/api/plan/generate')
  approvePlan = (by: string) => this.req<Plan>('POST', '/api/plan/approve', { by })
  exStart = (exercise_id: string, item: PlanItem | undefined, pain_before: number) =>
    this.req<RehabState>('POST', '/api/exercise/start', { exercise_id, item, pain_before })
  exStop = (pain_after?: number) => this.req<SessionResult>('POST', '/api/exercise/stop', { pain_after })
  exPain = (score: number) => this.req<{ stopped_exercise: boolean }>('POST', '/api/exercise/pain', { score, phase: 'during' })
  progress = () => this.req<Progress>('GET', '/api/progress')
  setLimits = (limits: Record<string, number>) => this.req<unknown>('PUT', '/api/recovery/limits', { limits, approved: false })
  linkCloud = (url: string, device_key: string) => this.req<{ patient_id: string }>('POST', '/api/cloud/link', { url, device_key })
  notes = () => this.req<{ id: string; author_name: string | null; ts: number; text: string }[]>('GET', '/api/notes')
  /** Binary download (PDF report, ZIP export). */
  async blob(path: string, timeoutMs = 60000): Promise<Blob> {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), timeoutMs)
    try {
      const r = await this.fetchImpl(this.baseUrl + path, { signal: ctl.signal })
      if (!r.ok) throw new ApiError(r.status, r.statusText)
      return await r.blob()
    } finally { clearTimeout(timer) }
  }
  report = (programId?: string | null) => this.blob(`/api/report?${programId ? `program_id=${programId}` : 'hours=24'}`)
  exportData = () => this.blob('/api/export')
  erase = (confirm: string) => this.req<{ erased: Record<string, number> }>('POST', '/api/data/erase', { confirm })
  get = <T>(path: string) => this.req<T>('GET', path)
  post = <T>(path: string, body?: unknown) => this.req<T>('POST', path, body ?? {})
  put = <T>(path: string, body: unknown) => this.req<T>('PUT', path, body)

  wsUrl(): string { return this.baseUrl.replace(/^http/, 'ws') + '/ws/live' }
}
