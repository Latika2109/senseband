// Pure logic for the `notify` Edge Function (web push + optional cloud SMS fallback).

export interface EventRow {
  id: string; patient_id: string; level: 'info' | 'warning' | 'critical'; kind: string; message: string | null
  status: string | null; sms_sent: number | null; acknowledged_by: string | null
}
export interface WebhookPayload { type: 'INSERT' | 'UPDATE'; record: EventRow; old_record: EventRow | null }

export type Decision = { push: boolean; cloudSms: boolean; reason: string }

/** Warnings/criticals are pushed when raised; a critical whose band SMS failed escalates to cloud SMS. */
export function decide(p: WebhookPayload): Decision {
  const e = p.record
  if (e.status === 'cancelled') return { push: false, cloudSms: false, reason: 'cancelled' }
  if (e.level === 'info') return { push: false, cloudSms: false, reason: 'info only' }
  if (p.type === 'INSERT') return { push: true, cloudSms: false, reason: 'raised' }
  const was = p.old_record?.status
  if (e.level === 'critical' && e.status === 'sms_failed' && was !== 'sms_failed') {
    return { push: true, cloudSms: true, reason: 'band SMS failed' }
  }
  if (e.level === 'critical' && e.status === 'sms_sent' && was !== 'sms_sent') {
    return { push: true, cloudSms: false, reason: 'SMS sent' }
  }
  return { push: false, cloudSms: false, reason: 'no change that matters' }
}

export interface Notification { title: string; body: string; tag: string; level: string; url: string; requireInteraction: boolean }

export function buildNotification(e: EventRow, patientName: string, reason: string): Notification {
  const prefix = e.level === 'critical' ? 'ALERT' : 'Warning'
  const extra = reason === 'SMS sent' ? ' SMS was sent to the contacts.' : reason === 'band SMS failed' ? ' The band could not send its SMS.' : ''
  return {
    title: `${prefix}: ${patientName}`, body: `${(e.message ?? e.kind).slice(0, 140)}${extra}`.slice(0, 200),
    tag: `event-${e.id}`, level: e.level, url: `/#/alerts`, requireInteraction: e.level === 'critical',
  }
}

export const smsText = (patientName: string, e: EventRow): string =>
  `SenseBand ALERT [${patientName}]: ${(e.message ?? e.kind)}`.slice(0, 160)

export function verifyWebhook(header: string | null, secret: string | undefined): boolean {
  if (!secret || !header) return false
  const a = header.replace(/^Bearer\s+/, '')
  if (a.length !== secret.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ secret.charCodeAt(i)
  return diff === 0
}
