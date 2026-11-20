import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { chooseMode, inviteCodeOk, latestPlan, limitsOf, nextVersion, otpPhone, triage, validatePlanEdit, vapidKey } from '../cloud/logic'
import type { CloudApi, CloudEvent, CloudPlan, Overview } from '../cloud/types'
import { en } from '../i18n/en'
import { hi } from '../i18n/hi'

const ov = (o: Partial<Overview>): Overview => ({ id: 'p', name: 'P', open_severity: 0, open_alerts: 0, last_event_ts: null, last_vitals: null, ...o })

describe('cloud logic', () => {
  it('local mode when the band answers, cloud mode when signed in, otherwise none', () => {
    expect(chooseMode(true, true)).toBe('local'); expect(chooseMode(true, false)).toBe('local')
    expect(chooseMode(false, true)).toBe('cloud'); expect(chooseMode(false, false)).toBe('none')
  })
  it('triage: critical first, then warning, more open alerts, most recent, then name', () => {
    const rows = [ov({ id: 'ok', name: 'Zed' }), ov({ id: 'w', name: 'W', open_severity: 2, open_alerts: 1 }), ov({ id: 'c1', name: 'C1', open_severity: 3, open_alerts: 1, last_event_ts: 5 }),
      ov({ id: 'c2', name: 'C2', open_severity: 3, open_alerts: 3, last_event_ts: 1 }), ov({ id: 'c3', name: 'C3', open_severity: 3, open_alerts: 1, last_event_ts: 9 }), ov({ id: 'ok2', name: 'Abe' })]
    expect(triage(rows).map((r) => r.id)).toEqual(['c2', 'c3', 'c1', 'w', 'ok2', 'ok'])
    expect(rows[0]!.id).toBe('ok')                                  // input not mutated
  })
  it('a doctor edit can never exceed the stored maximum angle and must be sensible', () => {
    const it = (o: object) => ({ exercise_id: 'x', movement: 'elbow_flexion', sets: 3, reps: 10, target_angle: 90, hold_s: 3, ...o })
    expect(validatePlanEdit([it({})], { elbow_flexion: 100 })).toEqual({})
    expect(validatePlanEdit([it({ target_angle: 101 })], { elbow_flexion: 100 })[0]).toBe('cloud.err.limit')
    expect(validatePlanEdit([it({ target_angle: 101 })], {})).toEqual({})
    expect(validatePlanEdit([it({ sets: 0 })], {})[0]).toBe('cloud.err.volume')
    expect(validatePlanEdit([it({ reps: 51 })], {})[0]).toBe('cloud.err.volume')
    expect(validatePlanEdit([it({ target_angle: 0 })], {})[0]).toBe('cloud.err.angle')
    expect(validatePlanEdit([it({}), it({ target_angle: 250 })], {})).toEqual({ 1: 'cloud.err.angle' })
  })
  it('plan versions: next is max+1 and the latest wins; limits read from the first case', () => {
    const p = (v: number): CloudPlan => ({ id: `p${v}`, patient_id: 'x', version: v, created_by: 'system', approved_by: null, stage: '1', items_json: { items: [] } })
    expect(nextVersion([])).toBe(1); expect(nextVersion([p(1), p(7), p(3)])).toBe(8); expect(latestPlan([p(1), p(7), p(3)])?.id).toBe('p7'); expect(latestPlan([])).toBeNull()
    expect(limitsOf([{ id: 'c', patient_id: 'x', joint: 'elbow', side: 'r', max_angle_limits_json: { elbow_flexion: 90 }, doctor_approved: 1 }])).toEqual({ elbow_flexion: 90 }); expect(limitsOf([])).toEqual({})
  })
  it('phone, invite code and VAPID helpers', () => {
    expect(otpPhone('98765 43210')).toBe('+919876543210'); expect(otpPhone('+44 7700 900123')).toBe('+447700900123'); expect(otpPhone('123')).toBeNull()
    expect(inviteCodeOk('ABCD1234')).toBe(true); expect(inviteCodeOk(' abcd1234 ')).toBe(true); for (const b of ['', 'ABC', 'ABCD-123', 'ABCD12345']) expect(inviteCodeOk(b)).toBe(false)
    expect([...vapidKey('AQID')]).toEqual([1, 2, 3]); expect([...vapidKey('-_8')]).toEqual([251, 255])
  })
  it('all cloud strings exist in Hindi with matching placeholders', () => {
    const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join()
    for (const k of Object.keys(en).filter((k) => k.startsWith('cloud.')) as (keyof typeof en)[]) { expect(hi[k], k).toBeTruthy(); expect(ph(hi[k])).toBe(ph(en[k])) }
  })
})

function fakeApi(over: Partial<CloudApi> = {}) {
  const state = { uid: null as string | null, name: null as string | null, role: 'doctor' as 'doctor' | 'caregiver' | 'patient', listener: null as ((e: CloudEvent) => void) | null }
  const calls: { fn: string; args: unknown[] }[] = []
  const rec = <T,>(fn: string, v: T) => (...args: unknown[]): Promise<T> => { calls.push({ fn, args }); return Promise.resolve(v) }
  const plan: CloudPlan = { id: 'pl1', patient_id: 'p-asha', version: 2, created_by: 'system', approved_by: null, stage: '2', items_json: { movement: 'elbow_flexion', items: [{ exercise_id: 'elbow_active_flexion', movement: 'elbow_flexion', sets: 3, reps: 15, target_angle: 110, hold_s: 3 }] } }
  const api: CloudApi = {
    sendOtp: rec('sendOtp', undefined) as CloudApi['sendOtp'],
    verifyOtp: (async (...args: unknown[]) => { calls.push({ fn: 'verifyOtp', args }); state.uid = 'u1'; return { userId: 'u1' } }) as CloudApi['verifyOtp'],
    userId: async () => state.uid, signOut: (async () => { state.uid = null }) as CloudApi['signOut'],
    profile: async () => (state.uid && state.name ? { id: state.uid, name: state.name, role_default: state.role } : null),
    saveProfile: (async (name: string, role: 'doctor') => { calls.push({ fn: 'saveProfile', args: [name, role] }); state.name = name; state.role = role }) as CloudApi['saveProfile'],
    overview: async () => [ov({ id: 'p-ok', name: 'Calm Person' }), ov({ id: 'p-asha', name: 'Asha Rao', open_severity: 3, open_alerts: 2, last_vitals: { minute_ts: 1, hr_ecg: 71.6, hr_ppg: null, spo2_avg: 97.2, temp: 36.5, activity: 'sitting', rhythm: 'regular' } })],
    vitals: async () => [{ minute_ts: 1000, hr_ecg: 70, hr_expected: 72, spo2_avg: 97, temp: 36.4, activity: 'sitting' }],
    events: async () => [{ id: 'e1', patient_id: 'p-asha', ts: 1000, level: 'critical', kind: 'fall', message: 'Possible fall', status: 'sms_sent', sms_sent: 1, acknowledged_by: null, ack_ts: null }],
    ecgStrip: async () => [0, 1, 0, -1, 0, 1, 0, -1], ack: rec('ack', undefined) as CloudApi['ack'],
    plans: async () => [plan], savePlan: rec('savePlan', undefined) as CloudApi['savePlan'],
    cases: async () => [{ id: 'c1', patient_id: 'p-asha', joint: 'elbow', side: 'right', max_angle_limits_json: { elbow_flexion: 100 }, doctor_approved: 1 }],
    setLimits: rec('setLimits', undefined) as CloudApi['setLimits'],
    notes: async () => [], addNote: rec('addNote', undefined) as CloudApi['addNote'],
    createPatient: rec('createPatient', undefined) as CloudApi['createPatient'],
    invite: rec('invite', 'ABCD1234') as CloudApi['invite'],
    acceptInvite: (async (code: string) => { calls.push({ fn: 'acceptInvite', args: [code] }); if (code === 'BADCODE1') throw new Error('invalid invite code') }) as CloudApi['acceptInvite'],
    team: async () => [{ user_id: 'u1', role: 'doctor', status: 'active', name: 'Dr. Rao' }, { user_id: 'u2', role: 'caregiver', status: 'active', name: 'Ravi' }],
    revoke: rec('revoke', undefined) as CloudApi['revoke'], deletePatient: rec('deletePatient', undefined) as CloudApi['deletePatient'], registerDevice: rec('registerDevice', 'k'.repeat(40)) as CloudApi['registerDevice'],
    subscribeEvents: (cb) => { state.listener = cb; return () => { state.listener = null } }, savePush: rec('savePush', undefined) as CloudApi['savePush'],
    ...over,
  }
  return { api, state, calls }
}

function mount(api: CloudApi, hash = '#/cloud/login') {
  localStorage.clear(); location.hash = hash
  vi.stubGlobal('WebSocket', class { onopen = null; onclose = null; onmessage = null; close() { /* noop */ } })
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
  return render(<App cloudApi={api} />)
}
const click = (name: RegExp | string) => userEvent.setup().click(screen.getByRole('button', { name }))

describe('cloud screens', () => {
  beforeEach(() => { vi.unstubAllGlobals() })

  it('phone OTP sign-in, then name and role on first login, lands on the patient list', async () => {
    const { api, calls } = fakeApi()
    mount(api)
    const u = userEvent.setup()
    await u.type(await screen.findByLabelText(/mobile number/i), '9876543210')
    await u.click(screen.getByRole('button', { name: /send code/i }))
    expect(calls.find((c) => c.fn === 'sendOtp')?.args).toEqual(['+919876543210'])
    await u.type(await screen.findByLabelText(/code from the sms/i), '123456')
    await u.click(screen.getByRole('button', { name: /^verify$/i }))
    await u.type(await screen.findByLabelText(/your name/i), 'Dr. Rao')
    await u.click(screen.getByLabelText(/doctor/i))
    await u.click(screen.getByRole('button', { name: /continue/i }))
    expect(await screen.findByRole('heading', { name: /patients/i })).toBeInTheDocument()
    expect(calls.find((c) => c.fn === 'saveProfile')?.args).toEqual(['Dr. Rao', 'doctor'])
  })

  it('rejects a bad phone number and a short code before calling the cloud', async () => {
    const { api, calls } = fakeApi()
    mount(api)
    const u = userEvent.setup()
    await u.type(await screen.findByLabelText(/mobile number/i), '12345')
    expect(await screen.findByText(/valid mobile number/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /send code/i })).toBeDisabled()
    expect(calls).toHaveLength(0)
  })

  it('shows "not set up" when no cloud is configured', async () => {
    localStorage.clear(); location.hash = '#/cloud/login'
    render(<App />)
    expect(await screen.findByText(/cloud is not set up/i)).toBeInTheDocument()
  })

  it('doctor list shows the critical patient first with a Critical badge and the open alert count', async () => {
    const { api, state } = fakeApi(); state.uid = 'u1'; state.name = 'Dr. Rao'
    mount(api, '#/cloud/patients')
    const links = await screen.findAllByRole('link')
    const patients = links.filter((l) => l.getAttribute('href')?.startsWith('#/cloud/patient/'))
    expect(patients[0]).toHaveTextContent('Asha Rao'); expect(patients[0]).toHaveTextContent(/critical/i); expect(patients[0]).toHaveTextContent(/2 open alerts/i)
    expect(patients[0]).toHaveTextContent(/HR 72/); expect(patients[1]).toHaveTextContent('Calm Person'); expect(patients[1]).toHaveTextContent(/no open alerts/i)
  })

  it('joining with an invite code validates the format and shows the cloud error', async () => {
    const { api, state, calls } = fakeApi(); state.uid = 'u1'; state.name = 'Dr. Rao'
    mount(api, '#/cloud/patients')
    const u = userEvent.setup()
    const box = await screen.findByLabelText(/have an invite code/i)
    await u.type(box, 'abc'); await click(/^join$/i)
    expect(await screen.findByText(/does not look right/i)).toBeInTheDocument(); expect(calls.some((c) => c.fn === 'acceptInvite')).toBe(false)
    await u.clear(box); await u.type(box, 'BADCODE1'); await click(/^join$/i)
    expect(await screen.findByText(/invalid invite code/i)).toBeInTheDocument()
  })

  it('doctor edits the plan: blocked above the stored limit, saved as the next version otherwise', async () => {
    const { api, state, calls } = fakeApi(); state.uid = 'u1'; state.name = 'Dr. Rao'
    mount(api, '#/cloud/patient/p-asha')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('tab', { name: /plan/i }))
    const angle = await screen.findByLabelText(/target °/i)
    expect(angle).toHaveValue('110')
    expect(await screen.findByText(/above the stored maximum angle/i)).toBeInTheDocument()       // 110 > limit 100 already
    expect(screen.getByRole('button', { name: /save as approved plan/i })).toBeDisabled()
    await u.clear(angle); await u.type(angle, '95')
    await waitFor(() => expect(screen.getByRole('button', { name: /save as approved plan/i })).toBeEnabled())
    await u.click(screen.getByRole('button', { name: /save as approved plan/i }))
    const save = calls.find((c) => c.fn === 'savePlan')!
    expect(save.args[0]).toBe('p-asha'); expect(save.args[5]).toBe(3)                                  // version 2 -> 3
    expect((save.args[3] as { target_angle: number }[])[0]!.target_angle).toBe(95)
    expect(await screen.findByText(/saved\. the band will pick it up/i)).toBeInTheDocument()
  })

  it('a caregiver sees the plan but cannot edit it or write notes', async () => {
    const { api, state } = fakeApi(); state.uid = 'u1'; state.name = 'Ravi'; state.role = 'caregiver'
    mount(api, '#/cloud/patient/p-asha')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('tab', { name: /plan/i }))
    expect(await screen.findByLabelText(/target °/i)).toBeDisabled()
    expect(screen.queryByRole('button', { name: /save as approved plan/i })).not.toBeInTheDocument()
    await u.click(screen.getByRole('tab', { name: /notes/i }))
    expect(await screen.findByText(/no notes yet/i)).toBeInTheDocument(); expect(screen.queryByPlaceholderText(/write a note/i)).not.toBeInTheDocument()
  })

  it('doctor adds a note, acknowledges an alert and sees the ECG strip', async () => {
    const { api, state, calls } = fakeApi(); state.uid = 'u1'; state.name = 'Dr. Rao'
    mount(api, '#/cloud/patient/p-asha')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('tab', { name: /notes/i }))
    await u.type(await screen.findByPlaceholderText(/write a note/i), 'Reduce reps to 8')
    await u.click(screen.getByRole('button', { name: /^add note$/i }))
    expect(calls.find((c) => c.fn === 'addNote')?.args).toEqual(['p-asha', 'Reduce reps to 8'])
    await u.click(screen.getByRole('tab', { name: /alerts/i }))
    expect(await screen.findByText('Possible fall')).toBeInTheDocument(); expect(screen.getByText(/sms sent to your contacts/i)).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: /ecg strip saved/i }))
    expect(await screen.findByRole('img', { name: /ecg strip/i })).toBeInTheDocument()
    await u.click(screen.getByRole('button', { name: /acknowledge/i }))
    expect(calls.find((c) => c.fn === 'ack')?.args).toEqual(['e1'])
  })

  it('care team: shows members, creates an invite code, can remove access from others but not yourself or the patient', async () => {
    const { api, state, calls } = fakeApi(); state.uid = 'u1'; state.name = 'Asha'; state.role = 'patient'
    mount(api, '#/cloud/patient/p-asha')
    const u = userEvent.setup()
    await u.click(await screen.findByRole('tab', { name: /care team/i }))
    expect(await screen.findByText('Ravi')).toBeInTheDocument()
    const revokes = screen.getAllByRole('button', { name: /remove access/i })
    expect(revokes).toHaveLength(1)                                                                  // only Ravi, not me (u1)
    await u.click(screen.getByRole('button', { name: /invite a doctor/i }))
    expect(await screen.findByText(/ABCD1234/)).toBeInTheDocument(); expect(calls.find((c) => c.fn === 'invite')?.args).toEqual(['p-asha', 'doctor'])
    await u.click(revokes[0]!); expect(calls.find((c) => c.fn === 'revoke')?.args).toEqual(['p-asha', 'u2'])
  })

  it('a realtime critical alert pops up immediately; info and cancelled alerts do not', async () => {
    const { api, state } = fakeApi(); state.uid = 'u1'; state.name = 'Dr. Rao'
    mount(api, '#/cloud/patients')
    await screen.findByRole('heading', { name: /patients/i })
    await waitFor(() => expect(state.listener).not.toBeNull())
    const ev = (o: Partial<CloudEvent>): CloudEvent => ({ id: 'x', patient_id: 'p-asha', ts: 1, level: 'critical', kind: 'fall', message: 'Possible fall', status: 'active', sms_sent: 0, acknowledged_by: null, ack_ts: null, ...o })
    state.listener!(ev({ id: 'i', level: 'info' })); state.listener!(ev({ id: 'c', status: 'cancelled' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    state.listener!(ev({ id: 'k' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/new critical: possible fall/i)
  })

  it('signing out returns to the login screen', async () => {
    const { api, state } = fakeApi(); state.uid = 'u1'; state.name = 'Dr. Rao'
    mount(api, '#/cloud/patients')
    await screen.findByRole('heading', { name: /patients/i })
    await click(/sign out/i)
    expect(await screen.findByLabelText(/mobile number/i)).toBeInTheDocument()
  })
})
