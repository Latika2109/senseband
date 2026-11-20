// Mirrors the edge API (docs/05_DATA_MODEL_AND_API.md). Keep in sync with edge/senseband_edge/api/app.py.
export type Level = 'info' | 'warning' | 'critical'
export type EcgQuality = 'collecting' | 'good' | 'noisy' | 'lead_off' | 'flat'
export type PpgQuality = 'none' | 'good' | 'poor' | 'finger_off'
export type Activity = 'lying' | 'sleeping' | 'sitting' | 'standing' | 'walking' | 'stairs' | 'exercising' | 'running' | 'unknown'
export type Rhythm = 'regular' | 'possible_irregular' | 'unknown'

export interface Vitals {
  t: 'vitals'; ts: number; hr: number | null; hr_src: 'ecg' | 'ppg' | null
  hr_expected: number; hr_range: [number, number]; context: Activity; posture: string
  intensity: number; spo2: number | null; temp: number | null; rhythm: Rhythm
  quality: { ecg: EcgQuality; ppg: PpgQuality }; ews: number; status: 'normal' | 'watch' | 'alert'
}
export interface EcgMsg { t: 'ecg'; ts: number; fs: number; samples: number[] }
export interface AlertInfo {
  id: string; level: Level; kind: string; message: string; raised_t: number
  status: 'notified' | 'active' | 'cancelled' | 'sms_pending' | 'sms_sent' | 'sms_failed'
  sms_sent: number; countdown_ends: number | null; countdown_left_s: number | null
  acknowledged_by: string | null
}
export interface EventMsg extends Omit<AlertInfo, never> { t: 'event'; why: string }
export interface Program {
  id: string; patient_id: string; type: 'monitoring' | 'recovery'; purpose: string
  start_ts: number; planned_end_ts: number | null; status: string
}
export interface Status {
  time: number; mode: string; uptime_s: number; data_time_s: number
  sensors: { ecg: EcgQuality; ppg: PpgQuality | null; imu: boolean; temp: boolean }
  band: { connected: boolean; battery_pct: number | null; last_seen: number | null; finger: boolean }
  source: Record<string, unknown>; sms: { modem: string; ok: boolean; error?: string | null }
  internet: boolean | null; sync_backlog: number; program: Program | null
  alerts: { countdown: AlertInfo[]; recent: AlertInfo[] }; buzzer: boolean | null
  rss_mb: number; errors: number; learning_since_ts: number | null; learning_h?: number
  models?: Record<string, { active: boolean; reason?: string; avg_ms?: number | null }>
}
export interface SignalCheck {
  ecg: { quality: EcgQuality; tip: string }; ppg: { quality: PpgQuality; tip: string }
  imu: { quality: string; activity: Activity; tip: string }; temp: { quality: string; tip: string }
  ready: boolean
}
export interface PairInfo { name: string; api: number; port: number; mode: string; patient_id: string; urls: string[]; qr: string }
export interface EventRow {
  id: string; ts: number; level: Level; kind: string; message: string; status: string
  sms_sent: number; acknowledged_by: string | null; ack_ts: number | null
}
export interface Contact { name: string; phone: string; relation: string }
export interface PatientRow {
  id: string; name: string; dob: string | null; sex: string | null; height_cm: number | null
  weight_kg: number | null; conditions: string[]; medications: string[]; resting_hr: number | null
  emergency_contacts: Contact[]
}
export type LiveMsg = Vitals | EcgMsg | EventMsg | ({ t: 'status' } & Status) | { t: 'ping' } | AngleMsg | RepMsg | LimitMsg | ExerciseDoneMsg

// ---- Milestone 3: recovery ----
export type Joint = 'shoulder' | 'elbow' | 'wrist' | 'hip' | 'knee' | 'ankle'
export interface AngleMsg {
  t: 'angle'; ts: number; mode: 'rom' | 'exercise'; movement: string; angle: number; joint?: Joint
  rep?: number; target?: number; limit?: number | null; in_target?: boolean; too_fast?: boolean; over_limit?: boolean
}
export interface RepMsg {
  t: 'rep'; ts: number; n: number; peak_deg: number; duration_s: number; up_s: number; down_s: number
  hold_s: number; too_fast: boolean; over_limit: boolean; set: number; rep_in_set: number
}
export interface LimitMsg { t: 'limit_exceeded'; ts: number; angle: number; limit: number | null }
export interface SessionResult {
  kind: 'session'; exercise_id: string; stopped_reason: 'completed' | 'user' | 'pain'; reps: number
  avg_peak_angle: number | null; max_angle: number; target_hit_pct: number | null; too_fast_count: number
  over_limit_count: number; target_angle: number; limit_angle: number | null; planned_reps: number
  duration_s: number; pain_before: number | null; pain_after: number | null
}
export interface ExerciseDoneMsg extends SessionResult { t: 'exercise_done' }
export interface ExerciseDef {
  id: string; joint: Joint; movement: string; stage: 1 | 2 | 3; sets: number; reps: number; hold_s: number
  tempo_s: [number, number]; target_pct: number; name: string; name_hi: string; how: string
}
export interface PlanItem {
  exercise_id: string; movement: string; sets: number; reps: number; target_angle: number
  hold_s: number; tempo_s: [number, number]; limit_angle: number | null
}
export interface Plan {
  id: string; version: number; stage: 1 | 2 | 3; created_by: string; approved_by: string | null
  status: 'approved' | 'waiting_for_doctor' | 'may_start_stage1'; items: PlanItem[]; notes: string[]
  rom_pct: number | null; movement: string | null
}
export interface RomResult {
  kind: 'rom'; movement: string; side: string | null; phase: string; min_angle: number; max_angle: number
  rom: number; normal_pct: number; over_limit: boolean; limit_angle: number | null
}
export interface RehabState {
  mode: 'idle' | 'calibrating' | 'rom' | 'exercise' | 'grip'; movement: string | null; angle: number | null
  calibrated: Joint[]; reps: number | null; target: number | null
  result: (Record<string, unknown> & { kind?: string; ok?: boolean; error?: string }) | null
}
export interface Forecast {
  ok: boolean; reason?: string; n: number; est_ts?: number | null; ci_low_ts?: number; ci_high_ts?: number
  confidence?: string; plateau_deg?: number
}
export interface Progress {
  case: { joint: Joint; side: 'left' | 'right'; max_angle_limits_json: Record<string, number> } | null
  movement?: string; normal_rom?: number; target?: number
  rom?: { day: number; rom: number; pct: number }[]
  grip?: { injured: { ts: number; peak: number }[]; healthy: { ts: number; peak: number }[] }
  sessions?: unknown[]; adherence?: number | null; forecast?: Forecast
}
