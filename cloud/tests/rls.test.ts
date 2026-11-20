// Milestone 4 acceptance: "a doctor account sees only invited patients" and the rest of the security model,
// verified on real PostgreSQL 16 with the actual migrations.
import type { PGlite } from '@electric-sql/pglite'
import { beforeEach, describe, expect, it } from 'vitest'
import { addUser, as, fails, freshDb, q } from './pg'

let db: PGlite
let asha: string, ravi: string, drRao: string, drMehta: string, stranger: string
const ev = (id: string, pid: string, level = 'warning', extra = '') =>
  q(db, `insert into public.events (id, patient_id, ts, level, kind, message ${extra ? ', ' + extra.split('=')[0] : ''}) values ($1,$2,1000,$3,'k','m' ${extra ? ', ' + extra.split('=')[1] : ''})`, [id, pid, level])

beforeEach(async () => {
  db = await freshDb()
  asha = await addUser(db, 'Asha', 'patient')
  ravi = await addUser(db, 'Ravi', 'caregiver')
  drRao = await addUser(db, 'Dr. Rao', 'doctor')
  drMehta = await addUser(db, 'Dr. Mehta', 'doctor')
  stranger = await addUser(db, 'Stranger')
  await as(db, asha, () => q(db, `select * from public.create_patient('p-asha', 'Asha')`))
  await as(db, stranger, () => q(db, `select * from public.create_patient('p-other', 'Other Person')`))
  await q(db, `insert into public.vitals_minute (patient_id, minute_ts, hr_ecg) values ('p-asha', 60000, 72), ('p-other', 60000, 90)`)
  await ev('e-asha', 'p-asha', 'critical')
  await ev('e-other', 'p-other', 'critical')
})

const invite = async (by: string, pid: string, role: string) => (await as(db, by, () => q<{ create_invite: string }>(db, `select public.create_invite($1,$2)`, [pid, role])))[0]!.create_invite
const accept = (uid: string, code: string) => as(db, uid, () => q(db, `select * from public.accept_invite($1)`, [code]))
const count = (uid: string | null, table: string) => as(db, uid, async () => (await q<{ n: number }>(db, `select count(*)::int as n from public.${table}`))[0]!.n)

describe('care team and visibility', () => {
  it('a doctor sees only the patients that invited them', async () => {
    const code = await invite(asha, 'p-asha', 'doctor')
    await accept(drRao, code)
    expect(await as(db, drRao, () => q(db, `select id from public.patients`))).toEqual([{ id: 'p-asha' }])
    expect(await count(drRao, 'vitals_minute')).toBe(1)
    expect(await as(db, drRao, () => q(db, `select id from public.events`))).toEqual([{ id: 'e-asha' }])
    // a different doctor, and a stranger, see nothing of Asha
    expect(await count(drMehta, 'patients')).toBe(0)
    expect(await count(drMehta, 'events')).toBe(0)
    expect(await as(db, stranger, () => q(db, `select id from public.patients`))).toEqual([{ id: 'p-other' }])
    expect(await fails(count(null, 'patients'))).toMatch(/permission denied/)   // anon has no grants at all
    expect(await fails(as(db, null, () => q(db, `select * from public.events`)))).toMatch(/permission denied/)
  })

  it('the patient and caregiver see their data; revoking removes access immediately', async () => {
    await accept(ravi, await invite(asha, 'p-asha', 'caregiver'))
    expect(await count(ravi, 'vitals_minute')).toBe(1)
    await accept(drRao, await invite(ravi, 'p-asha', 'doctor'))                      // a caregiver may invite a doctor
    expect(await count(drRao, 'events')).toBe(1)
    await as(db, asha, () => q(db, `select public.revoke_access('p-asha', $1)`, [drRao]))
    expect(await count(drRao, 'events')).toBe(0)
    expect(await count(drRao, 'patients')).toBe(0)
    expect((await as(db, asha, () => q(db, `select status from public.care_team where user_id=$1`, [drRao])))[0]).toEqual({ status: 'revoked' })
    await as(db, ravi, () => q(db, `select public.revoke_access('p-asha', $1)`, [ravi]))   // leaving yourself
    expect(await count(ravi, 'events')).toBe(0)
  })

  it('invites: single use, expiry, valid role, who may create them, case-insensitive code', async () => {
    const code = await invite(asha, 'p-asha', 'doctor')
    await accept(drRao, code.toLowerCase())
    expect(await fails(accept(drMehta, code))).toMatch(/already used/)
    expect(await fails(accept(drMehta, 'NOPE1234'))).toMatch(/invalid/)
    await q(db, `insert into public.invites (code, patient_id, role, created_by, expires_at) values ('OLDCODE1','p-asha','doctor',$1, now() - interval '1 day')`, [asha])
    expect(await fails(accept(drMehta, 'OLDCODE1'))).toMatch(/expired/)
    expect(await fails(as(db, drRao, () => q(db, `select public.create_invite('p-asha','doctor')`)))).toMatch(/not allowed/)   // doctors cannot invite
    expect(await fails(as(db, stranger, () => q(db, `select public.create_invite('p-asha','doctor')`)))).toMatch(/not allowed/)
    expect(await fails(as(db, asha, () => q(db, `select public.create_invite('p-asha','patient')`)))).toMatch(/role/)
    expect(await fails(as(db, null, () => q(db, `select public.accept_invite('X')`)))).toMatch(/permission denied/)
  })

  it('only the owner-side roles can revoke others, and nobody can revoke the patient', async () => {
    await accept(drRao, await invite(asha, 'p-asha', 'doctor'))
    await accept(ravi, await invite(asha, 'p-asha', 'caregiver'))
    expect(await fails(as(db, drRao, () => q(db, `select public.revoke_access('p-asha', $1)`, [ravi])))).toMatch(/not allowed/)
    expect(await fails(as(db, ravi, () => q(db, `select public.revoke_access('p-asha', $1)`, [asha])))).toMatch(/cannot be revoked/)
    expect(await fails(as(db, stranger, () => q(db, `select public.revoke_access('p-asha', $1)`, [drRao])))).toMatch(/not allowed/)
  })

  it('profiles are visible only to yourself and your care team', async () => {
    await accept(drRao, await invite(asha, 'p-asha', 'doctor'))
    const seen = (await as(db, drRao, () => q<{ name: string }>(db, `select name from public.profiles order by name`))).map((r) => r.name)
    expect(seen).toEqual(['Asha', 'Dr. Rao'])
    const mine = await as(db, stranger, () => q<{ name: string }>(db, `select name from public.profiles`))
    expect(mine.map((r) => r.name)).toEqual(['Stranger'])
    expect(await fails(as(db, stranger, () => q(db, `update public.profiles set name='x' where id=$1`, [asha])).then(async () => { if ((await q(db, `select name from public.profiles where id=$1`, [asha]))[0]!.name === 'x') return; throw new Error('blocked as expected') }))).toMatch(/blocked as expected/)
  })
})

describe('what each role may write', () => {
  beforeEach(async () => {
    await accept(drRao, await invite(asha, 'p-asha', 'doctor'))
    await accept(ravi, await invite(asha, 'p-asha', 'caregiver'))
  })

  it('nobody can write measurements directly (only the band, via ingest)', async () => {
    for (const uid of [asha, ravi, drRao]) {
      expect(await fails(as(db, uid, () => q(db, `insert into public.events (id, patient_id, ts, level, kind) values ('x','p-asha',1,'info','k')`)))).toMatch(/permission denied/)
      expect(await fails(as(db, uid, () => q(db, `update public.vitals_minute set hr_ecg = 1`)))).toMatch(/permission denied/)
      expect(await fails(as(db, uid, () => q(db, `delete from public.events`)))).toMatch(/permission denied/)
    }
  })

  it('a doctor can add notes and plans (stamped with their identity); caregivers and patients cannot', async () => {
    const n = await as(db, drRao, () => q<{ author_name: string; id: string; cloud_edited: boolean }>(db,
      `insert into public.notes (patient_id, text) values ('p-asha', 'Reduce reps to 8') returning author_name, id, cloud_edited`))
    expect(n[0]).toMatchObject({ author_name: 'Dr. Rao', cloud_edited: true }); expect(n[0]!.id).toHaveLength(36)
    for (const uid of [ravi, asha]) expect(await fails(as(db, uid, () => q(db, `insert into public.notes (patient_id, text) values ('p-asha','x')`)))).toMatch(/row-level security/)
    expect(await fails(as(db, drMehta, () => q(db, `insert into public.notes (patient_id, text) values ('p-asha','x')`)))).toMatch(/row-level security/)
    expect(await count(asha, 'notes')).toBe(1)                          // the patient can read the note
    const p = await as(db, drRao, () => q<{ created_by: string; approved_by: string }>(db,
      `insert into public.exercise_plans (patient_id, version, stage, approved_by, items_json) values ('p-asha', 3, '2', 'ignored', '{"items":[]}') returning created_by, approved_by`))
    expect(p[0]).toEqual({ created_by: 'doctor', approved_by: 'Dr. Rao' })      // identity comes from the profile
    expect(await fails(as(db, ravi, () => q(db, `insert into public.exercise_plans (patient_id, version) values ('p-asha', 4)`)))).toMatch(/row-level security/)
  })

  it('a doctor can set the maximum-angle limits; the patient cannot', async () => {
    await q(db, `insert into public.recovery_cases (id, patient_id, program_id, joint, side, max_angle_limits_json) values ('c1','p-asha','pr1','elbow','right','{}')`)
    await as(db, drRao, () => q(db, `update public.recovery_cases set max_angle_limits_json='{"elbow_flexion":100}', doctor_approved=1 where id='c1'`))
    const row = (await q<{ max_angle_limits_json: object; cloud_edited: boolean; doctor_approved: number }>(db, `select * from public.recovery_cases`))[0]!
    expect(row).toMatchObject({ max_angle_limits_json: { elbow_flexion: 100 }, cloud_edited: true, doctor_approved: 1 })
    await as(db, asha, () => q(db, `update public.recovery_cases set max_angle_limits_json='{"elbow_flexion":170}' where id='c1'`))
    expect((await q<{ max_angle_limits_json: object }>(db, `select max_angle_limits_json from public.recovery_cases`))[0]!.max_angle_limits_json).toEqual({ elbow_flexion: 100 })
  })

  it('patient/caregiver may edit the profile, a doctor may not', async () => {
    await as(db, ravi, () => q(db, `update public.patients set medications='["Metoprolol"]' where id='p-asha'`))
    expect((await q<{ medications: string[] }>(db, `select medications from public.patients where id='p-asha'`))[0]!.medications).toEqual(['Metoprolol'])
    await as(db, drRao, () => q(db, `update public.patients set medications='[]' where id='p-asha'`))
    expect((await q<{ medications: string[] }>(db, `select medications from public.patients where id='p-asha'`))[0]!.medications).toEqual(['Metoprolol'])
    expect(await fails(as(db, asha, () => q(db, `insert into public.patients (id, name) values ('hack','x')`)))).toMatch(/permission denied/)
  })

  it('ack_event is allowed to care team only and records who', async () => {
    const r = await as(db, drRao, () => q<{ acknowledged_by: string }>(db, `select * from public.ack_event('e-asha')`))
    expect(r[0]!.acknowledged_by).toBe('Dr. Rao')
    expect(await fails(as(db, drMehta, () => q(db, `select public.ack_event('e-asha')`)))).toMatch(/not allowed/)
    expect(await fails(as(db, drRao, () => q(db, `select public.ack_event('e-other')`)))).toMatch(/not allowed/)
  })

  it('triage view lists only your patients, most severe unacknowledged first', async () => {
    await as(db, asha, () => q(db, `select * from public.create_patient('p-asha2', 'Asha second')`))
    await ev('e-w', 'p-asha2', 'warning')
    await accept(drRao, await invite(asha, 'p-asha2', 'doctor'))
    const rows = await as(db, drRao, () => q<{ id: string; open_severity: number; open_alerts: number }>(db,
      `select id, open_severity, open_alerts from public.patient_overview order by open_severity desc, open_alerts desc`))
    expect(rows).toEqual([{ id: 'p-asha', open_severity: 3, open_alerts: 1 }, { id: 'p-asha2', open_severity: 2, open_alerts: 1 }])
    await as(db, drRao, () => q(db, `select public.ack_event('e-asha')`))
    const after = await as(db, drRao, () => q<{ id: string; open_severity: number }>(db, `select id, open_severity from public.patient_overview order by open_severity desc`))
    expect(after[0]).toEqual({ id: 'p-asha2', open_severity: 2 })
    expect((await as(db, drRao, () => q<{ last_vitals: { hr_ecg: number } }>(db, `select last_vitals from public.patient_overview where id='p-asha'`)))[0]!.last_vitals.hr_ecg).toBe(72)
  })

  it('cancelled alerts do not count as open', async () => {
    await q(db, `update public.events set status='cancelled' where id='e-asha'`)
    expect((await as(db, drRao, () => q<{ open_alerts: number }>(db, `select open_alerts from public.patient_overview`)))[0]!.open_alerts).toBe(0)
  })
})

describe('devices and ingest', () => {
  let key: string
  const row = (table: string, row_id: string, payload: object) => ({ table, row_id, op: 'upsert', payload })
  const ingest = (rows: object[], k = key) => as(db, null, () => q<{ ingest_batch: { row_id: string; status: string; error?: string }[] }>(db, `select public.ingest_batch($1, $2::jsonb)`, [k, JSON.stringify(rows)])).then((r) => r[0]!.ingest_batch)
  beforeEach(async () => { key = (await as(db, asha, () => q<{ register_device: string }>(db, `select public.register_device('p-asha','SN-1')`)))[0]!.register_device })

  it('registering a device stores only a hash; the key is never readable', async () => {
    const stored = (await q<{ key_hash: string }>(db, `select key_hash from public.devices`))[0]!.key_hash
    expect(stored).toHaveLength(64); expect(stored).not.toContain(key)
    expect(await fails(as(db, asha, () => q(db, `select key_hash from public.devices`)))).toMatch(/permission denied/)
    expect(await as(db, asha, () => q(db, `select id, main_serial from public.devices`))).toHaveLength(1)
    expect(await fails(as(db, drRao, () => q(db, `select public.register_device('p-asha')`)))).toMatch(/not allowed/)
    expect(await count(drRao, 'devices')).toBe(0)
  })

  it('rejects a wrong key and anything but the anon+device-key path', async () => {
    expect(await fails(ingest([], 'wrong'))).toMatch(/invalid device key/)
    expect(await fails(as(db, null, () => q(db, `select public.device_for_key($1)`, [key])))).toMatch(/permission denied/)
    expect(await fails(as(db, asha, () => q(db, `select public.ingest_batch($1,'[]')`, ['bad'])))).toMatch(/invalid device key/)
  })

  it('upserts edge payloads for its own patient and is idempotent (re-sync creates no duplicates)', async () => {
    const batch = [
      row('events', 'ev1', { id: 'ev1', patient_id: 'p-asha', ts: 5, level: 'critical', kind: 'fall', message: 'm', context_json: { hr: 100 }, sms_sent: 1, status: 'sms_sent', extra_unknown: 1 }),
      row('vitals_minute', 'p-asha:120000', { patient_id: 'p-asha', minute_ts: 120000, hr_ecg: 70, quality_flags: ['ecg_noisy'], ews: 0 }),
      row('programs', 'pr1', { id: 'pr1', patient_id: 'p-asha', type: 'monitoring', purpose: 'general', start_ts: 1, status: 'active', settings_json: {} }),
      row('assessments', 'a1', { id: 'a1', program_id: 'pr1', ts: 3, kind: 'rom', movement: 'elbow_flexion', rom: 96.5, phase: 'baseline' }),
      row('ecg_strips', 's1', { id: 's1', event_id: 'ev1', ts: 5, fs: 250, samples_b64: 'AAAA' }),
    ]
    const r1 = await ingest(batch)
    expect(r1.map((x) => x.status)).toEqual(['ok', 'ok', 'ok', 'ok', 'ok'])
    const snapshot = async () => ({ ev: await count(drRaoOrAsha(), 'events'), v: await count(asha, 'vitals_minute'), a: await count(asha, 'assessments') })
    const drRaoOrAsha = () => asha
    const s1 = await snapshot()
    const r2 = await ingest([...batch, ...batch])                     // lost response: the whole batch is resent, twice
    expect(r2.every((x) => x.status === 'ok')).toBe(true)
    expect(await snapshot()).toEqual(s1)
    expect(await as(db, asha, () => q(db, `select hr_ecg from public.vitals_minute where minute_ts = 120000`))).toEqual([{ hr_ecg: 70 }])
    const a = (await as(db, asha, () => q<{ patient_id: string; rom: number }>(db, `select patient_id, rom from public.assessments`)))[0]!
    expect(a).toEqual({ patient_id: 'p-asha', rom: 96.5 })              // patient_id stamped by the server
    await ingest([row('events', 'ev1', { id: 'ev1', patient_id: 'p-asha', ts: 5, level: 'critical', kind: 'fall', acknowledged_by: 'Ravi', ack_ts: 9, status: 'sms_sent' })])
    expect((await as(db, asha, () => q<{ acknowledged_by: string }>(db, `select acknowledged_by from public.events where id='ev1'`)))[0]!.acknowledged_by).toBe('Ravi')   // later versions update
    expect((await as(db, asha, () => q<{ storage_path: string | null }>(db, `select storage_path from public.ecg_strips`)))[0]!.storage_path).toBeNull()
  })

  it('a band can only ever write its own patient, and never another patient\'s rows', async () => {
    const res = await ingest([
      row('events', 'x1', { id: 'x1', patient_id: 'p-other', ts: 1, level: 'info', kind: 'k' }),     // wrong patient in payload
      row('events', 'e-other', { id: 'e-other', ts: 1, level: 'info', kind: 'hijack', message: 'pwned' }),   // someone else's id
      row('vitals_minute', 'v', { patient_id: 'p-other', minute_ts: 60000, hr_ecg: 1 }),
      row('patients', 'p-other', { id: 'p-other', name: 'Renamed' }),
      row('care_team', 'c', { patient_id: 'p-asha', user_id: stranger, role: 'doctor' }),           // not an allowed table
      row('devices', 'd', { id: 'd', patient_id: 'p-asha', key_hash: 'x' }),
      row('events', 'good', { id: 'good', ts: 2, level: 'info', kind: 'ok' }),                      // one good row in the same batch
    ])
    expect(res.map((x) => x.status)).toEqual(['error', 'error', 'error', 'error', 'error', 'error', 'ok'])
    expect(res[1]!.error).toMatch(/another patient/); expect(res[4]!.error).toMatch(/not allowed/)
    const other = await as(db, stranger, () => q<{ kind: string; level: string }>(db, `select kind, level from public.events where id='e-other'`))
    expect(other[0]).toEqual({ kind: 'k', level: 'critical' })                                    // untouched
    expect((await as(db, stranger, () => q<{ hr_ecg: number }>(db, `select hr_ecg from public.vitals_minute`)))[0]!.hr_ecg).toBe(90)
    expect((await as(db, stranger, () => q<{ name: string }>(db, `select name from public.patients`)))[0]!.name).toBe('Other Person')
    expect(await as(db, asha, () => q(db, `select id from public.events where id = 'good'`))).toHaveLength(1)
  })

  it('malformed rows fail individually and never poison the batch', async () => {
    const res = await ingest([{ table: 'events', row_id: 'a', payload: 'not-an-object' }, { table: null, row_id: 'b', payload: {} },
      row('events', 'c', { id: 'c', ts: 'not-a-number', level: 'info', kind: 'k' }), row('events', 'd', { id: 'd', ts: 1, level: 'bogus', kind: 'k' }),
      row('events', 'e', { id: 'e', ts: 1, level: 'info', kind: 'k' })])
    expect(res.map((x) => x.status)).toEqual(['error', 'error', 'error', 'error', 'ok'])
  })

  it('cloud wins for profile, plans, limits and notes once a human edited them', async () => {
    await ingest([row('patients', 'p-asha', { id: 'p-asha', name: 'Asha (band)', conditions: ['diabetes'], dob: '1959-01-01', created_at: 5 })])
    const p0 = (await as(db, asha, () => q<{ name: string; conditions: string[]; cloud_edited: boolean }>(db, `select name, conditions, cloud_edited from public.patients`)))[0]!
    expect(p0).toEqual({ name: 'Asha (band)', conditions: ['diabetes'], cloud_edited: false })   // first sync fills the cloud row
    await as(db, ravi === ravi ? asha : asha, () => q(db, `update public.patients set name='Asha Rao' where id='p-asha'`))
    expect((await q<{ cloud_edited: boolean }>(db, `select cloud_edited from public.patients where id='p-asha'`))[0]!.cloud_edited).toBe(true)
    const res = await ingest([row('patients', 'p-asha', { id: 'p-asha', name: 'Asha (band again)' })])
    expect(res[0]!.status).toBe('skipped')                                                        // device cannot overwrite the cloud edit
    expect((await q<{ name: string }>(db, `select name from public.patients where id='p-asha'`))[0]!.name).toBe('Asha Rao')
    // a plan the device synced first can still be superseded by the doctor
    await ingest([row('exercise_plans', 'pl1', { id: 'pl1', version: 1, created_by: 'system', stage: '1', items_json: { items: [] } })])
    await accept(drRao, await invite(asha, 'p-asha', 'doctor'))
    await as(db, drRao, () => q(db, `update public.exercise_plans set approved_by='x', stage='2' where id='pl1'`))
    expect((await ingest([row('exercise_plans', 'pl1', { id: 'pl1', version: 1, stage: '1' })]))[0]!.status).toBe('skipped')
    expect((await q<{ stage: string; approved_by: string }>(db, `select stage, approved_by from public.exercise_plans where id='pl1'`))[0]).toEqual({ stage: '2', approved_by: 'Dr. Rao' })
  })

  it('pull_changes returns only this band\'s doctor/patient edits after the cursor', async () => {
    await accept(drRao, await invite(asha, 'p-asha', 'doctor'))
    const pull = (since?: string) => as(db, null, () => q<{ pull_changes: Record<string, unknown[]> & { cursor: string } }>(db, `select public.pull_changes($1, coalesce($2::timestamptz, 'epoch'))`, [key, since ?? null])).then((r) => r[0]!.pull_changes)
    const p0 = await pull()
    expect(p0).toMatchObject({ patients: [], exercise_plans: [], notes: [], recovery_cases: [] })
    await as(db, drRao, () => q(db, `insert into public.notes (patient_id, text) values ('p-asha', 'Walk daily')`))
    await as(db, drRao, () => q(db, `insert into public.exercise_plans (patient_id, version, stage, items_json) values ('p-asha', 2, '2', '{"items":[1]}')`))
    await as(db, asha, () => q(db, `update public.patients set resting_hr = 62 where id='p-asha'`))
    await as(db, stranger, () => q(db, `update public.patients set resting_hr = 99 where id='p-other'`))
    const p1 = await pull()
    expect(p1.notes).toHaveLength(1); expect(p1.exercise_plans).toHaveLength(1); expect(p1.patients).toHaveLength(1)
    expect((p1.patients[0] as { resting_hr: number }).resting_hr).toBe(62)
    expect(JSON.stringify(p1)).not.toContain('p-other'); expect(JSON.stringify(p1)).not.toContain('owner')
    const p2 = await pull(p1.cursor)
    expect(p2).toMatchObject({ patients: [], exercise_plans: [], notes: [] }); expect(p2.cursor).toBe(p1.cursor)
    await as(db, drRao, () => q(db, `insert into public.notes (patient_id, text) values ('p-asha', 'Second')`))
    expect((await pull(p1.cursor)).notes).toHaveLength(1)
    expect(await fails(as(db, null, () => q(db, `select public.pull_changes('wrong')`)))).toMatch(/invalid device key/)
  })
})

describe('misc', () => {
  it('push subscriptions are private to their owner', async () => {
    await as(db, asha, () => q(db, `insert into public.push_subscriptions (endpoint, p256dh, auth) values ('https://push/1','k','a')`))
    expect(await count(asha, 'push_subscriptions')).toBe(1)
    expect(await count(ravi, 'push_subscriptions')).toBe(0)
    expect(await fails(as(db, ravi, () => q(db, `insert into public.push_subscriptions (user_id, endpoint, p256dh, auth) values ($1,'https://push/2','k','a')`, [asha])))).toMatch(/row-level security/)
  })

  it('DPDP erasure: only the patient can delete everything, and it really deletes', async () => {
    await accept(drRao, await invite(asha, 'p-asha', 'doctor'))
    await accept(ravi, await invite(asha, 'p-asha', 'caregiver'))
    for (const uid of [drRao, ravi]) expect(await fails(as(db, uid, () => q(db, `select public.delete_patient_data('p-asha')`)))).toMatch(/only the patient/)
    await as(db, asha, () => q(db, `select public.delete_patient_data('p-asha')`))
    for (const t of ['patients', 'vitals_minute', 'events', 'care_team', 'invites']) expect(await q(db, `select 1 from public.${t} where ${t === 'patients' ? 'id' : 'patient_id'} = 'p-asha'`)).toEqual([])
    expect(await count(stranger, 'patients')).toBe(1)                // other patients untouched
    expect(await count(stranger, 'vitals_minute')).toBe(1)
  })

  it('create_patient needs a signed-in user and validates input and roles', async () => {
    expect(await fails(as(db, null, () => q(db, `select public.create_patient('x','y')`)))).toMatch(/permission denied/)
    expect(await fails(as(db, asha, () => q(db, `select public.create_patient('x','y','doctor')`)))).toMatch(/role/)
    expect(await fails(as(db, asha, () => q(db, `select public.create_patient('','y')`)))).toMatch(/required/)
    expect(await fails(as(db, asha, () => q(db, `select public.create_patient('p-asha','dup')`)))).toMatch(/duplicate/)
    const ct = await as(db, ravi, async () => { await q(db, `select public.create_patient('p-ravi','Mum','caregiver')`); return q<{ role: string }>(db, `select role from public.care_team`) })
    expect(ct).toEqual([{ role: 'caregiver' }])
  })

  it('creating a patient in the cloud does not count as a cloud edit (the band\'s richer profile can still sync)', async () => {
    expect((await q<{ cloud_edited: boolean }>(db, `select cloud_edited from public.patients where id='p-asha'`))[0]!.cloud_edited).toBe(false)
  })
})
