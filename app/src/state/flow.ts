// Onboarding order and "where should this user be?" - pure so it can be tested.
export const SETUP_ROUTES = ['/welcome', '/profile', '/pair', '/program', '/purpose', '/duration', '/wear', '/check'] as const

export interface FlowState {
  profileDone: boolean; bandUrl: string | null; programActive: boolean
  programType: 'monitoring' | 'recovery' | null; purpose: string | null; durationChosen: boolean
  wearDone: boolean
}

/** First incomplete setup step, or '/live' when monitoring is already running on the band. */
export function nextRoute(s: FlowState): string {
  if (s.programActive && s.bandUrl && s.profileDone) return s.programType === 'recovery' ? '/rehab' : '/live'
  if (!s.profileDone) return '/welcome'
  if (!s.bandUrl) return '/pair'
  if (!s.programType) return '/program'
  if (s.programType === 'recovery') return '/recovery'
  if (!s.purpose) return '/purpose'
  if (!s.durationChosen) return '/duration'
  if (!s.wearDone) return '/wear'
  return '/check'
}

export function stepOf(path: string): { n: number; total: number } | null {
  const i = SETUP_ROUTES.indexOf(path as (typeof SETUP_ROUTES)[number])
  return i < 1 ? null : { n: i, total: SETUP_ROUTES.length - 1 }
}

export interface CheckItem { id: 'ecg' | 'ppg' | 'imu' | 'battery' | 'sms' | 'internet'; state: 'pass' | 'fail' | 'wait' | 'skipped' | 'info'; label?: string; required: boolean }

import type { SignalCheck, Status } from '../api/types'

export function evaluateChecks(sc: SignalCheck | null, st: Status | null, purpose: string | null): CheckItem[] {
  const athlete = purpose === 'athlete'
  const q = (ok: boolean, have: boolean): CheckItem['state'] => (!have ? 'wait' : ok ? 'pass' : 'fail')
  const batt = st?.band.battery_pct
  return [
    { id: 'ecg', state: q(sc?.ecg.quality === 'good', !!sc && sc.ecg.quality !== 'collecting'), required: true },
    { id: 'ppg', state: athlete ? 'skipped' : q(sc?.ppg.quality === 'good', !!sc), required: !athlete },
    { id: 'imu', state: q(!!sc && sc.imu.activity !== 'unknown', !!sc), required: true },
    { id: 'battery', state: batt == null ? 'wait' : batt >= 20 ? 'pass' : 'fail', required: false },
    { id: 'sms', state: st ? (st.sms.ok ? 'pass' : 'fail') : 'wait', required: true },
    { id: 'internet', state: 'info', required: false },
  ]
}
export const requiredPass = (items: CheckItem[]) => items.filter((i) => i.required).every((i) => i.state === 'pass' || i.state === 'skipped')
