import { describe, expect, it } from 'vitest'
import { bearer, extractStrips, mapResults, parseBody } from '../supabase/functions/_shared/ingest'
import { buildNotification, decide, smsText, verifyWebhook, type EventRow } from '../supabase/functions/_shared/notify'

const row = (id: number, table: string, payload: Record<string, unknown> = {}) => ({ id, table, row_id: `r${id}`, op: 'upsert', payload })

describe('ingest helpers', () => {
  it('validates the body shape and limits', () => {
    expect(parseBody({ rows: [row(1, 'events', { id: 'a' })] })).toHaveProperty('rows')
    for (const bad of [null, {}, { rows: 'x' }, { rows: [{}] }, { rows: [{ id: '1', table: 't', row_id: 'r', payload: {} }] }, { rows: [{ id: 1, table: 't', row_id: 'r', payload: null }] }]) expect(parseBody(bad)).toHaveProperty('error')
    expect(parseBody({ rows: Array.from({ length: 501 }, (_, i) => row(i, 'events')) })).toHaveProperty('error')
  })
  it('moves ECG blobs to storage paths scoped to the patient and strips the blob from the row', () => {
    const b64 = btoa(String.fromCharCode(1, 2, 3, 4))
    const { rows, strips } = extractStrips([row(1, 'ecg_strips', { id: 's/../1', event_id: 'e', samples_b64: b64 }), row(2, 'events', { id: 'e' })], 'p-asha')
    expect(strips).toHaveLength(1)
    expect(strips[0]!.path).toBe('p-asha/s1.f32')                       // path traversal characters removed
    expect([...strips[0]!.bytes]).toEqual([1, 2, 3, 4])
    expect(rows[0]!.payload).toEqual({ id: 's/../1', event_id: 'e', storage_path: 'p-asha/s1.f32' })
    expect(rows[1]!.payload).toEqual({ id: 'e' })
  })
  it('maps database results to outbox ids: ok and skipped are accepted, errors are rejected', () => {
    const sent = [row(1, 'events'), row(2, 'events'), row(3, 'events'), row(4, 'events')]
    const r = mapResults(sent, [{ row_id: 'r1', status: 'ok' }, { row_id: 'r2', status: 'skipped' }, { row_id: 'r3', status: 'error', error: 'boom' }])
    expect(r.accepted).toEqual([1, 2]); expect(r.rejected).toEqual([{ id: 3, error: 'boom' }, { id: 4, error: 'no result for row' }])
  })
  it('extracts bearer tokens strictly', () => {
    expect(bearer('Bearer abcdefghijklmnop1234')).toBe('abcdefghijklmnop1234')
    for (const bad of [null, '', 'Bearer short', 'Basic abcdefghijklmnop1234', 'Bearer a b c d e f g h i j k l m n o p']) expect(bearer(bad)).toBeNull()
  })
})

const ev = (o: Partial<EventRow> = {}): EventRow => ({ id: 'e1', patient_id: 'p', level: 'critical', kind: 'fall', message: 'Possible fall - no movement detected.', status: 'active', sms_sent: 0, acknowledged_by: null, ...o })

describe('notify decisions', () => {
  it('pushes warnings and criticals when raised, never info or cancelled', () => {
    expect(decide({ type: 'INSERT', record: ev(), old_record: null })).toMatchObject({ push: true, cloudSms: false })
    expect(decide({ type: 'INSERT', record: ev({ level: 'warning' }), old_record: null }).push).toBe(true)
    expect(decide({ type: 'INSERT', record: ev({ level: 'info' }), old_record: null }).push).toBe(false)
    expect(decide({ type: 'UPDATE', record: ev({ status: 'cancelled' }), old_record: ev() }).push).toBe(false)
  })
  it('escalates to cloud SMS exactly once when the band SMS failed', () => {
    expect(decide({ type: 'UPDATE', record: ev({ status: 'sms_failed' }), old_record: ev({ status: 'sms_pending' }) })).toMatchObject({ push: true, cloudSms: true })
    expect(decide({ type: 'UPDATE', record: ev({ status: 'sms_failed' }), old_record: ev({ status: 'sms_failed' }) }).cloudSms).toBe(false)
    expect(decide({ type: 'UPDATE', record: ev({ level: 'warning', status: 'sms_failed' }), old_record: ev() }).cloudSms).toBe(false)
    expect(decide({ type: 'UPDATE', record: ev({ status: 'sms_sent' }), old_record: ev({ status: 'sms_pending' }) })).toMatchObject({ push: true, cloudSms: false })
    expect(decide({ type: 'UPDATE', record: ev({ acknowledged_by: 'x' }), old_record: ev() }).push).toBe(false)
  })
  it('builds short, non-diagnostic notification text', () => {
    const n = buildNotification(ev({ message: 'x'.repeat(500) }), 'Asha', 'raised')
    expect(n.title).toBe('ALERT: Asha'); expect(n.body.length).toBeLessThanOrEqual(200); expect(n.requireInteraction).toBe(true)
    expect(buildNotification(ev({ level: 'warning' }), 'Asha', 'raised')).toMatchObject({ title: 'Warning: Asha', requireInteraction: false })
    expect(buildNotification(ev(), 'Asha', 'band SMS failed').body).toMatch(/could not send/)
    expect(smsText('Asha', ev({ message: 'y'.repeat(400) })).length).toBe(160)
  })
  it('verifies the webhook secret in constant time and refuses when unset', () => {
    expect(verifyWebhook('Bearer s3cret', 's3cret')).toBe(true)
    expect(verifyWebhook('Bearer nope!!', 's3cret')).toBe(false)
    expect(verifyWebhook('Bearer s3cret', undefined)).toBe(false)
    expect(verifyWebhook(null, 's3cret')).toBe(false)
  })
})
