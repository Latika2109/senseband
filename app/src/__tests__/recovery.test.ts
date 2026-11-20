import { describe, expect, it } from 'vitest'
import { initialLive, liveReducer } from '../api/live'
import type { AngleMsg, RepMsg } from '../api/types'
import { arcPath, emptyRecovery, forecastView, MAIN_MOVEMENT, MOVEMENTS_BY_JOINT, needleDeg, NORMAL_ROM, recoveryValid, toRecoveryCase, validateRecovery } from '../state/recovery'
import { en } from '../i18n/en'

describe('recovery draft', () => {
  const ok = { ...emptyRecovery(), joint: 'elbow' as const, side: 'right' as const, injuryDate: '2026-10-20' }
  const today = new Date('2026-11-20')
  it('needs a real past injury date', () => {
    expect(recoveryValid(validateRecovery(ok, today))).toBe(true)
    expect(validateRecovery({ ...ok, injuryDate: '' }, today).injuryDate).toBe('rec.err.date')
    expect(validateRecovery({ ...ok, injuryDate: '20-10-2026' }, today).injuryDate).toBe('rec.err.date')
    expect(validateRecovery({ ...ok, injuryDate: '2027-01-01' }, today).injuryDate).toBe('rec.err.date')
    expect(validateRecovery({ ...ok, castRemoved: 'x' }, today).castRemoved).toBe('rec.err.date')
  })
  it('validates the doctor limit range and builds the band payload', () => {
    expect(validateRecovery({ ...ok, limits: { elbow_flexion: '5' } }, today).limits?.elbow_flexion).toBe('rec.err.limit')
    expect(validateRecovery({ ...ok, limits: { elbow_flexion: '' } }, today).limits).toBeUndefined()
    expect(toRecoveryCase({ ...ok, limits: { elbow_flexion: '110', forearm_rotation: '' }, castRemoved: '2026-11-01' })).toEqual({
      joint: 'elbow', side: 'right', injury_type: 'fracture', injury_date: '2026-10-20', cast_removed_date: '2026-11-01',
      weight_bearing: null, max_angle_limits: { elbow_flexion: 110 },
    })
  })
  it('knows every movement of the six joints with a normal range', () => {
    expect(Object.keys(MOVEMENTS_BY_JOINT).sort()).toEqual(['ankle', 'elbow', 'hip', 'knee', 'shoulder', 'wrist'])
    for (const [j, ms] of Object.entries(MOVEMENTS_BY_JOINT)) {
      expect(ms).toContain(MAIN_MOVEMENT[j as keyof typeof MAIN_MOVEMENT])
      for (const m of ms) { expect(NORMAL_ROM[m], m).toBeGreaterThan(0); expect(en[`movement.${m}` as keyof typeof en], m).toBeTruthy() }
    }
  })
})

describe('gauge + forecast text', () => {
  it('maps angles onto a 180 degree dial and clamps', () => {
    expect(needleDeg(0, 150)).toBe(0); expect(needleDeg(75, 150)).toBe(90); expect(needleDeg(300, 150)).toBe(180); expect(needleDeg(-5, 150)).toBe(0)
    expect(arcPath(100, 100, 50, 0, 180)).toMatch(/^M 50\.00 100\.00 A 50 50 0 0 1 150\.00 100\.00$/)
  })
  it('tells the patient how many more days are needed and never promises a date', () => {
    expect(forecastView(undefined, 2)).toEqual({ kind: 'need', more: 3 })
    expect(forecastView({ ok: false, n: 3 }, 4)).toEqual({ kind: 'need', more: 1 })
    expect(forecastView({ ok: true, n: 8, reason: 'target_not_reachable_on_trend' }, 8)).toEqual({ kind: 'plateau' })
    expect(forecastView({ ok: true, n: 8, est_ts: Date.now() - 1000 }, 8)).toEqual({ kind: 'done' })
    const future = Date.now() + 5 * 86400000
    expect(forecastView({ ok: true, n: 8, est_ts: future, ci_low_ts: future - 1, ci_high_ts: future + 1 }, 8)).toMatchObject({ kind: 'date', est: future, n: 8 })
    expect(en['rec.prog.forecast.estimate']).toMatch(/estimate/i)
  })
})

describe('live store for exercises', () => {
  const angle = (a: number): AngleMsg => ({ t: 'angle', ts: 1, mode: 'exercise', movement: 'elbow_flexion', angle: a, rep: 1, target: 100, in_target: false, too_fast: false, over_limit: false })
  it('keeps a bounded angle trace, rep list and the final summary, and resets between sessions', () => {
    let s = initialLive
    for (let i = 0; i < 260; i++) s = liveReducer(s, { type: 'msg', msg: angle(i), now: 0 })
    expect(s.angleTrace.length).toBe(200); expect(s.angle?.angle).toBe(259)
    const rep: RepMsg = { t: 'rep', ts: 5, n: 1, peak_deg: 99, duration_s: 6, up_s: 3, down_s: 3, hold_s: 2, too_fast: false, over_limit: false, set: 1, rep_in_set: 1 }
    s = liveReducer(s, { type: 'msg', msg: rep, now: 0 })
    s = liveReducer(s, { type: 'msg', msg: { t: 'limit_exceeded', ts: 6, angle: 120, limit: 110 }, now: 777 })
    expect(s.reps).toHaveLength(1); expect(s.limitExceeded).toBe(777)
    s = liveReducer(s, { type: 'msg', msg: { t: 'exercise_done', kind: 'session', exercise_id: 'x', stopped_reason: 'pain', reps: 3, avg_peak_angle: 90, max_angle: 95, target_hit_pct: 66, too_fast_count: 1, over_limit_count: 0, target_angle: 100, limit_angle: null, planned_reps: 10, duration_s: 30, pain_before: 2, pain_after: 8 }, now: 0 })
    expect(s.session?.stopped_reason).toBe('pain')
    s = liveReducer(s, { type: 'rehab_reset' })
    expect(s.angle).toBeNull(); expect(s.reps).toEqual([]); expect(s.session).toBeNull(); expect(s.angleTrace).toEqual([])
  })
})
