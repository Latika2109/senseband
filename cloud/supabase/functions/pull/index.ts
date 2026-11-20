// Edge Function: GET /functions/v1/pull?since=<cursor>   (the band pulls profile/plans/limits/notes)
import { createClient } from 'jsr:@supabase/supabase-js@2'
import { bearer } from '../_shared/ingest.ts'

const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, { auth: { persistSession: false } })
const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

Deno.serve(async (req) => {
  const key = bearer(req.headers.get('Authorization'))
  if (!key) return json({ error: 'missing device key' }, 401)
  const since = new URL(req.url).searchParams.get('since') || 'epoch'
  const res = await db.rpc('pull_changes', { p_device_key: key, p_since: since })
  if (res.error) return json({ error: res.error.message }, /invalid device key/.test(res.error.message) ? 401 : 502)
  return json(res.data)
})
