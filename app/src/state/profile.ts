import type { Contact, PatientRow } from '../api/types'
import type { Key } from '../i18n'

export interface ProfileDraft {
  name: string; age: string; sex: '' | 'f' | 'm' | 'o'; height: string; weight: string
  conditions: string; medicines: string; beta: boolean; restingHr: string
  contacts: { name: string; phone: string }[]
}
export const emptyProfile = (): ProfileDraft => ({
  name: '', age: '', sex: '', height: '', weight: '', conditions: '', medicines: '', beta: false,
  restingHr: '', contacts: [{ name: '', phone: '' }],
})

/** India-first: a bare 10-digit mobile starting 6-9 becomes +91…; others need a leading + or 8-15 digits. */
export function normalizePhone(raw: string): string | null {
  const d = raw.replace(/[\s\-().]/g, '')
  if (/^[6-9]\d{9}$/.test(d)) return '+91' + d
  if (/^0[6-9]\d{9}$/.test(d)) return '+91' + d.slice(1)
  if (/^\+\d{8,15}$/.test(d)) return d
  if (/^91[6-9]\d{9}$/.test(d)) return '+' + d
  return null
}

export type Errors = Partial<Record<'name' | 'age' | 'height' | 'weight' | 'restingHr' | 'contacts', Key>>
  & { contactPhones?: Record<number, Key> }

const inRange = (s: string, lo: number, hi: number) => { const n = Number(s); return Number.isFinite(n) && n >= lo && n <= hi }

export function validateProfile(p: ProfileDraft): Errors {
  const e: Errors = {}
  if (p.name.trim().length < 2) e.name = 'err.name'
  if (!/^\d+$/.test(p.age.trim()) || !inRange(p.age, 1, 120)) e.age = 'err.age'
  if (p.height.trim() && !inRange(p.height, 30, 250)) e.height = 'err.number'
  if (p.weight.trim() && !inRange(p.weight, 2, 400)) e.weight = 'err.number'
  if (p.restingHr.trim() && !inRange(p.restingHr, 25, 150)) e.restingHr = 'err.number'
  const filled = p.contacts.map((c, i) => ({ c, i })).filter(({ c }) => c.phone.trim() || c.name.trim())
  const phones: Record<number, Key> = {}
  for (const { c, i } of filled) if (!normalizePhone(c.phone)) phones[i] = 'err.phone'
  if (Object.keys(phones).length) e.contactPhones = phones
  if (!filled.length) e.contacts = 'err.contact'
  return e
}
export const isValid = (e: Errors) => Object.keys(e).length === 0

const split = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)

/** Draft -> what the band stores. Age is stored as an approximate date of birth (the band keeps dob). */
export function toPatientPayload(p: ProfileDraft, now = new Date()): Partial<PatientRow> {
  const meds = split(p.medicines)
  if (p.beta && !meds.some((m) => /beta|metoprolol|atenolol/i.test(m))) meds.push('beta-blocker')
  const contacts: Contact[] = p.contacts
    .map((c) => ({ name: c.name.trim() || 'Contact', phone: normalizePhone(c.phone) ?? '', relation: '' }))
    .filter((c) => c.phone)
  const out: Partial<PatientRow> = {
    name: p.name.trim(), dob: `${now.getFullYear() - Number(p.age)}-01-01`, conditions: split(p.conditions),
    medications: meds, emergency_contacts: contacts,
  }
  if (p.sex) out.sex = p.sex
  if (p.height.trim()) out.height_cm = Number(p.height)
  if (p.weight.trim()) out.weight_kg = Number(p.weight)
  if (p.restingHr.trim()) out.resting_hr = Number(p.restingHr)
  return out
}
