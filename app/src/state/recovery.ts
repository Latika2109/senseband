import type { Forecast, Joint } from '../api/types'
import type { Key } from '../i18n'

export interface RecoveryDraft {
  joint: Joint | null; side: 'left' | 'right' | null; injuryType: 'fracture' | 'surgery' | 'sprain'
  injuryDate: string; castRemoved: string; weightBearing: string; limits: Record<string, string>; pain: number | null
}
export const emptyRecovery = (): RecoveryDraft => ({ joint: null, side: null, injuryType: 'fracture', injuryDate: '', castRemoved: '', weightBearing: '', limits: {}, pain: null })

export const MOVEMENTS_BY_JOINT: Record<Joint, string[]> = {
  shoulder: ['shoulder_flexion', 'shoulder_abduction', 'shoulder_rotation'],
  elbow: ['elbow_flexion', 'forearm_rotation'],
  wrist: ['wrist_flexion', 'wrist_extension', 'wrist_deviation'],
  hip: ['hip_flexion', 'hip_abduction'],
  knee: ['knee_flexion'],
  ankle: ['ankle_dorsiflexion', 'ankle_plantarflexion'],
}
export const MAIN_MOVEMENT: Record<Joint, string> = {
  shoulder: 'shoulder_flexion', elbow: 'elbow_flexion', wrist: 'wrist_flexion', hip: 'hip_flexion', knee: 'knee_flexion', ankle: 'ankle_dorsiflexion',
}
export const ARM_JOINTS: Joint[] = ['shoulder', 'elbow', 'wrist']
export const NORMAL_ROM: Record<string, number> = {
  shoulder_flexion: 180, shoulder_abduction: 180, shoulder_rotation: 90, elbow_flexion: 150, forearm_rotation: 80, wrist_flexion: 80,
  wrist_extension: 70, wrist_deviation: 45, hip_flexion: 120, hip_abduction: 45, knee_flexion: 135, ankle_dorsiflexion: 20, ankle_plantarflexion: 50,
}

const isDate = (s: string) => /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s))

export type RecErrors = Partial<Record<'injuryDate' | 'castRemoved', Key>> & { limits?: Record<string, Key> }

export function validateRecovery(d: RecoveryDraft, today = new Date()): RecErrors {
  const e: RecErrors = {}
  if (!isDate(d.injuryDate) || Date.parse(d.injuryDate) > today.getTime()) e.injuryDate = 'rec.err.date'
  if (d.castRemoved && (!isDate(d.castRemoved) || Date.parse(d.castRemoved) > today.getTime())) e.castRemoved = 'rec.err.date'
  const lim: Record<string, Key> = {}
  for (const [k, v] of Object.entries(d.limits)) if (v.trim() && !(Number(v) >= 10 && Number(v) <= 200)) lim[k] = 'rec.err.limit'
  if (Object.keys(lim).length) e.limits = lim
  return e
}
export const recoveryValid = (e: RecErrors) => Object.keys(e).length === 0

export function toRecoveryCase(d: RecoveryDraft): Record<string, unknown> {
  const limits: Record<string, number> = {}
  for (const [k, v] of Object.entries(d.limits)) if (v.trim()) limits[k] = Number(v)
  return {
    joint: d.joint, side: d.side, injury_type: d.injuryType, injury_date: d.injuryDate,
    cast_removed_date: d.castRemoved || null, weight_bearing: d.weightBearing || null, max_angle_limits: limits,
  }
}

/** Gauge geometry: angle -> needle degrees on a 180° dial. */
export function needleDeg(angle: number, max: number): number {
  return Math.max(0, Math.min(180, (angle / max) * 180))
}
export function arcPath(cx: number, cy: number, r: number, fromDeg: number, toDeg: number): string {
  const p = (deg: number) => { const a = Math.PI - (deg * Math.PI) / 180; return [cx + r * Math.cos(a), cy - r * Math.sin(a)] as const }
  const [x0, y0] = p(fromDeg), [x1, y1] = p(toDeg)
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${toDeg - fromDeg > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`
}

export type ForecastView =
  | { kind: 'need'; more: number }
  | { kind: 'date'; est: number; lo?: number; hi?: number; n: number }
  | { kind: 'plateau' } | { kind: 'done' }

/** What to tell the patient about the forecast. Never a promise: always shown as an estimate. */
export function forecastView(f: Forecast | undefined, sessions: number, minSessions = 5): ForecastView {
  if (!f || !f.ok) return { kind: 'need', more: Math.max(1, minSessions - sessions) }
  if (f.reason === 'target_not_reachable_on_trend') return { kind: 'plateau' }
  if (f.est_ts == null) return { kind: 'plateau' }
  if (f.est_ts <= Date.now()) return { kind: 'done' }
  return { kind: 'date', est: f.est_ts, lo: f.ci_low_ts, hi: f.ci_high_ts, n: f.n }
}
