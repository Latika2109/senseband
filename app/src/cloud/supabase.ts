import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import { load } from '../api/storage'
import type { CloudApi, CloudCase, CloudEvent, CloudNote, CloudPlan, Overview, Profile, TeamMember, VitalsRow } from './types'

export interface CloudConfig { url: string; anonKey: string; vapidPublicKey?: string }

/** Build-time env first, then a value saved in Settings (for self-hosted projects). */
export function cloudConfig(): CloudConfig | null {
  const env = import.meta.env as Record<string, string | undefined>
  const url = env.VITE_SUPABASE_URL, anonKey = env.VITE_SUPABASE_ANON_KEY
  if (url && anonKey) return { url, anonKey, vapidPublicKey: env.VITE_VAPID_PUBLIC_KEY }
  const saved = load<Partial<CloudConfig> | null>('cloud', null)
  return saved?.url && saved.anonKey ? (saved as CloudConfig) : null
}

const must = <T>(r: { data: T | null; error: { message: string } | null }): T => {
  if (r.error) throw new Error(r.error.message)
  return r.data as T
}

export function createSupabaseApi(cfg: CloudConfig, client: SupabaseClient = createClient(cfg.url, cfg.anonKey)): CloudApi {
  return {
    async sendOtp(phone) { const r = await client.auth.signInWithOtp({ phone }); if (r.error) throw new Error(r.error.message) },
    async verifyOtp(phone, code) {
      const r = await client.auth.verifyOtp({ phone, token: code, type: 'sms' })
      if (r.error || !r.data.user) throw new Error(r.error?.message ?? 'verification failed')
      return { userId: r.data.user.id }
    },
    async userId() { return (await client.auth.getUser()).data.user?.id ?? null },
    async signOut() { await client.auth.signOut() },
    async profile() {
      const uid = (await client.auth.getUser()).data.user?.id
      if (!uid) return null
      return must(await client.from('profiles').select('id,name,role_default').eq('id', uid).maybeSingle()) as Profile | null
    },
    async saveProfile(name, role) {
      const uid = (await client.auth.getUser()).data.user?.id
      must(await client.from('profiles').upsert({ id: uid, name, role_default: role }).select())
    },
    async overview() {
      const rows = must(await client.from('patient_overview').select('*')) as Overview[]
      return rows
    },
    async vitals(pid, since) {
      return must(await client.from('vitals_minute').select('minute_ts,hr_ecg,hr_expected,spo2_avg,temp,activity').eq('patient_id', pid).gte('minute_ts', since).order('minute_ts')) as VitalsRow[]
    },
    async events(pid, limit = 100) {
      return must(await client.from('events').select('*').eq('patient_id', pid).order('ts', { ascending: false }).limit(limit)) as CloudEvent[]
    },
    async ecgStrip(eventId) {
      const row = must(await client.from('ecg_strips').select('storage_path').eq('event_id', eventId).maybeSingle()) as { storage_path: string } | null
      if (!row?.storage_path) return null
      const dl = await client.storage.from('ecg-strips').download(row.storage_path)
      if (dl.error || !dl.data) return null
      const f = new Float32Array(await dl.data.arrayBuffer())
      const step = Math.max(1, Math.floor(f.length / 3000))
      return Array.from(f).filter((_, i) => i % step === 0)
    },
    async ack(id) { must(await client.rpc('ack_event', { p_event: id })) },
    async plans(pid) { return must(await client.from('exercise_plans').select('*').eq('patient_id', pid).order('version', { ascending: false })) as CloudPlan[] },
    async savePlan(pid, programId, stage, items, movement, version) {
      must(await client.from('exercise_plans').insert({ patient_id: pid, program_id: programId, version, stage, approved_by: 'self', items_json: { items, movement } }).select())
    },
    async cases(pid) { return must(await client.from('recovery_cases').select('*').eq('patient_id', pid)) as CloudCase[] },
    async setLimits(caseId, limits) {
      must(await client.from('recovery_cases').update({ max_angle_limits_json: limits, doctor_approved: 1 }).eq('id', caseId).select())
    },
    async notes(pid) { return must(await client.from('notes').select('*').eq('patient_id', pid).order('ts', { ascending: false })) as CloudNote[] },
    async addNote(pid, text) { must(await client.from('notes').insert({ patient_id: pid, text }).select()) },
    async createPatient(id, name, role) { must(await client.rpc('create_patient', { p_id: id, p_name: name, p_role: role })) },
    async invite(pid, role) { return must(await client.rpc('create_invite', { p_patient: pid, p_role: role })) as string },
    async acceptInvite(code) { must(await client.rpc('accept_invite', { p_code: code })) },
    async team(pid) {
      const rows = must(await client.from('care_team').select('user_id,role,status,profiles(name)').eq('patient_id', pid)) as unknown as { user_id: string; role: TeamMember['role']; status: TeamMember['status']; profiles: { name: string | null } | null }[]
      return rows.map((r) => ({ user_id: r.user_id, role: r.role, status: r.status, name: r.profiles?.name ?? null }))
    },
    async revoke(pid, uid) { must(await client.rpc('revoke_access', { p_patient: pid, p_user: uid })) },
    subscribeEvents(cb) {
      const ch = client.channel('events-live').on('postgres_changes', { event: '*', schema: 'public', table: 'events' }, (p) => cb(p.new as CloudEvent)).subscribe()
      return () => { void client.removeChannel(ch) }
    },
    async deletePatient(pid) { must(await client.rpc('delete_patient_data', { p_patient: pid })) },
    async registerDevice(pid, serial) { return must(await client.rpc('register_device', { p_patient: pid, p_serial: serial })) as string },
    async savePush(sub) { must(await client.from('push_subscriptions').upsert({ endpoint: sub.endpoint, p256dh: sub.p256dh, auth: sub.auth }, { onConflict: 'endpoint' }).select()) },
  }
}
