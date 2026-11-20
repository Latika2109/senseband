import type { Level } from '../api/types'

export type Role = 'patient' | 'caregiver' | 'doctor'
export interface Profile { id: string; name: string | null; role_default: Role }
export interface Overview {
  id: string; name: string; open_severity: number; open_alerts: number; last_event_ts: number | null
  last_vitals: { minute_ts: number; hr_ecg: number | null; hr_ppg: number | null; spo2_avg: number | null; temp: number | null; activity: string | null; rhythm: string | null } | null
}
export interface VitalsRow { minute_ts: number; hr_ecg: number | null; hr_expected: number | null; spo2_avg: number | null; temp: number | null; activity: string | null }
export interface CloudEvent {
  id: string; patient_id: string; ts: number; level: Level; kind: string; message: string | null
  status: string | null; sms_sent: number | null; acknowledged_by: string | null; ack_ts: number | null
}
export interface CloudPlanItem { exercise_id: string; movement: string; sets: number; reps: number; target_angle: number; hold_s: number; tempo_s?: [number, number]; limit_angle?: number | null }
export interface CloudPlan { id: string; patient_id: string; version: number; created_by: string; approved_by: string | null; stage: string; items_json: { items: CloudPlanItem[]; notes?: string[]; movement?: string } }
export interface CloudCase { id: string; patient_id: string; joint: string; side: string; max_angle_limits_json: Record<string, number> | null; doctor_approved: number }
export interface CloudNote { id: string; patient_id: string; author_name: string | null; ts: number; text: string }
export interface TeamMember { user_id: string; role: Role; status: 'invited' | 'active' | 'revoked'; name: string | null }

/** Everything the UI needs from the cloud. The Supabase implementation is in supabase.ts; tests use a fake. */
export interface CloudApi {
  sendOtp(phone: string): Promise<void>
  verifyOtp(phone: string, code: string): Promise<{ userId: string }>
  userId(): Promise<string | null>
  signOut(): Promise<void>
  profile(): Promise<Profile | null>
  saveProfile(name: string, role: Role): Promise<void>
  overview(): Promise<Overview[]>
  vitals(patientId: string, sinceMs: number): Promise<VitalsRow[]>
  events(patientId: string, limit?: number): Promise<CloudEvent[]>
  ecgStrip(storagePathOrEventId: string): Promise<number[] | null>
  ack(eventId: string): Promise<void>
  plans(patientId: string): Promise<CloudPlan[]>
  savePlan(patientId: string, programId: string | null, stage: string, items: CloudPlanItem[], movement: string | undefined, version: number): Promise<void>
  cases(patientId: string): Promise<CloudCase[]>
  setLimits(caseId: string, limits: Record<string, number>): Promise<void>
  notes(patientId: string): Promise<CloudNote[]>
  addNote(patientId: string, text: string): Promise<void>
  createPatient(id: string, name: string, role: 'patient' | 'caregiver'): Promise<void>
  invite(patientId: string, role: 'caregiver' | 'doctor'): Promise<string>
  acceptInvite(code: string): Promise<void>
  team(patientId: string): Promise<TeamMember[]>
  revoke(patientId: string, userId: string): Promise<void>
  subscribeEvents(onEvent: (e: CloudEvent) => void): () => void
  deletePatient(patientId: string): Promise<void>
  registerDevice(patientId: string, serial: string | null): Promise<string>
  savePush(sub: { endpoint: string; p256dh: string; auth: string }): Promise<void>
}
