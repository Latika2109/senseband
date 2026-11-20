// Edge Function: database webhook on public.events (INSERT/UPDATE) -> web push to the care team,
// and a cloud-SMS fallback (MSG91 or Twilio) when the band's own SMS failed. NOT run in CI.
// Secrets: WEBHOOK_SECRET, VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT, optional SMS_PROVIDER + keys.
import { createClient } from 'jsr:@supabase/supabase-js@2'
import webpush from 'npm:web-push@3.6.7'
import { buildNotification, decide, smsText, verifyWebhook, type WebhookPayload } from '../_shared/notify.ts'

const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, { auth: { persistSession: false } })
webpush.setVapidDetails(Deno.env.get('VAPID_SUBJECT') ?? 'mailto:admin@example.org', Deno.env.get('VAPID_PUBLIC_KEY')!, Deno.env.get('VAPID_PRIVATE_KEY')!)

async function sendSms(to: string, text: string): Promise<boolean> {
  const provider = Deno.env.get('SMS_PROVIDER')
  if (provider === 'twilio') {
    const sid = Deno.env.get('TWILIO_SID')!, tok = Deno.env.get('TWILIO_TOKEN')!
    const r = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${sid}/Messages.json`, {
      method: 'POST', headers: { Authorization: 'Basic ' + btoa(`${sid}:${tok}`), 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ To: to, From: Deno.env.get('TWILIO_FROM')!, Body: text }),
    })
    return r.ok
  }
  if (provider === 'msg91') {
    const r = await fetch('https://control.msg91.com/api/v5/flow/', {
      method: 'POST', headers: { authkey: Deno.env.get('MSG91_KEY')!, 'Content-Type': 'application/json' },
      body: JSON.stringify({ template_id: Deno.env.get('MSG91_TEMPLATE')!, recipients: [{ mobiles: to.replace('+', ''), message: text }] }),
    })
    return r.ok
  }
  return false
}

Deno.serve(async (req) => {
  if (!verifyWebhook(req.headers.get('Authorization'), Deno.env.get('WEBHOOK_SECRET'))) return new Response('forbidden', { status: 403 })
  const p = (await req.json()) as WebhookPayload
  const d = decide(p)
  if (!d.push && !d.cloudSms) return new Response(JSON.stringify({ skipped: d.reason }), { status: 200 })
  const { data: patient } = await db.from('patients').select('name, emergency_contacts').eq('id', p.record.patient_id).single()
  const name = patient?.name ?? 'Patient'
  const note = buildNotification(p.record, name, d.reason)
  let pushed = 0
  if (d.push) {
    const { data: team } = await db.from('care_team').select('user_id').eq('patient_id', p.record.patient_id).eq('status', 'active')
    const ids = (team ?? []).map((t) => t.user_id)
    const { data: subs } = await db.from('push_subscriptions').select('*').in('user_id', ids)
    for (const s of subs ?? []) {
      try {
        await webpush.sendNotification({ endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } }, JSON.stringify(note))
        pushed++
      } catch (e) {
        if ((e as { statusCode?: number }).statusCode === 404 || (e as { statusCode?: number }).statusCode === 410) await db.from('push_subscriptions').delete().eq('id', s.id)
      }
    }
  }
  let smsOk = 0
  if (d.cloudSms) {
    for (const c of (patient?.emergency_contacts ?? []) as { phone?: string }[]) if (c.phone && await sendSms(c.phone, smsText(name, p.record))) smsOk++
  }
  return new Response(JSON.stringify({ pushed, smsOk, reason: d.reason }), { status: 200 })
})
