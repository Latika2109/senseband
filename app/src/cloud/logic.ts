import type { CloudCase, CloudPlan, CloudPlanItem, Overview } from './types'
import type { Key } from '../i18n'

export type Mode = 'local' | 'cloud' | 'none'

/** Local mode when the band answers on the network; otherwise cloud mode if signed in (docs/02 rule 4). */
export function chooseMode(bandReachable: boolean, signedIn: boolean): Mode {
  return bandReachable ? 'local' : signedIn ? 'cloud' : 'none'
}

/** Doctor triage order: unacknowledged critical first, then warnings, then most open alerts, then most recent. */
export function triage(rows: Overview[]): Overview[] {
  return [...rows].sort((a, b) =>
    b.open_severity - a.open_severity || b.open_alerts - a.open_alerts || (b.last_event_ts ?? 0) - (a.last_event_ts ?? 0) || a.name.localeCompare(b.name))
}

export const SEVERITY: Record<number, { key: Key; level: 'ok' | 'warning' | 'critical' | 'info' }> = {
  0: { key: 'cloud.sev.0', level: 'ok' }, 1: { key: 'cloud.sev.1', level: 'info' }, 2: { key: 'cloud.sev.2', level: 'warning' }, 3: { key: 'cloud.sev.3', level: 'critical' },
}

export type PlanErrors = Record<number, Key>

/** A doctor's edit may never exceed the stored maximum angle; sets/reps must be sensible. */
export function validatePlanEdit(items: CloudPlanItem[], limits: Record<string, number>): PlanErrors {
  const e: PlanErrors = {}
  items.forEach((it, i) => {
    const lim = limits[it.movement]
    if (!(it.sets >= 1 && it.sets <= 10 && it.reps >= 1 && it.reps <= 50) || !(it.hold_s >= 0 && it.hold_s <= 60)) e[i] = 'cloud.err.volume'
    else if (!(it.target_angle > 0 && it.target_angle <= 200)) e[i] = 'cloud.err.angle'
    else if (lim != null && it.target_angle > lim) e[i] = 'cloud.err.limit'
  })
  return e
}

export const nextVersion = (plans: CloudPlan[]): number => plans.reduce((m, p) => Math.max(m, p.version), 0) + 1
export const latestPlan = (plans: CloudPlan[]): CloudPlan | null => plans.slice().sort((a, b) => b.version - a.version)[0] ?? null
export const limitsOf = (cases: CloudCase[]): Record<string, number> => ({ ...(cases[0]?.max_angle_limits_json ?? {}) })

/** Normalise a typed Indian mobile number for OTP sign-in. */
export function otpPhone(raw: string): string | null {
  const d = raw.replace(/[\s\-().]/g, '')
  if (/^[6-9]\d{9}$/.test(d)) return '+91' + d
  if (/^\+\d{8,15}$/.test(d)) return d
  return null
}

export const inviteCodeOk = (s: string): boolean => /^[A-Za-z0-9]{8}$/.test(s.trim())

/** Convert a base64url VAPID key for PushManager.subscribe. */
export function vapidKey(b64: string): Uint8Array {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}
