// Edge Function: POST /functions/v1/ingest   (the band's push endpoint)
// Authorization: Bearer <device key>; body {"rows":[{id, table, row_id, op, payload}]}
// Reply: {"accepted":[outbox ids], "rejected":[{"id","error"}]}.  NOT run in CI (needs Supabase/Deno).
import { createClient } from 'jsr:@supabase/supabase-js@2'
import { bearer, extractStrips, mapResults, MAX_BODY_BYTES, parseBody, type RowResult } from '../_shared/ingest.ts'

const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, { auth: { persistSession: false } })
const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

Deno.serve(async (req) => {
  if (req.method !== 'POST') return json({ error: 'POST only' }, 405)
  const key = bearer(req.headers.get('Authorization'))
  if (!key) return json({ error: 'missing device key' }, 401)
  const raw = await req.text()
  if (raw.length > MAX_BODY_BYTES) return json({ error: 'body too large' }, 413)
  let parsed
  try { parsed = parseBody(JSON.parse(raw)) } catch { return json({ error: 'invalid JSON' }, 400) }
  if ('error' in parsed) return json(parsed, 400)

  const dev = await db.rpc('device_for_key', { p_key: key })
  if (dev.error || !dev.data) return json({ error: 'invalid device key' }, 401)

  const { rows, strips } = extractStrips(parsed.rows, dev.data.patient_id)
  for (const s of strips) {       // idempotent: same path, upsert
    const up = await db.storage.from('ecg-strips').upload(s.path, s.bytes, { upsert: true, contentType: 'application/octet-stream' })
    if (up.error) return json({ error: `storage: ${up.error.message}` }, 502)   // band retries the whole batch
  }
  const res = await db.rpc('ingest_batch', { p_device_key: key, p_rows: rows.map(({ table, row_id, op, payload }) => ({ table, row_id, op, payload })) })
  if (res.error) return json({ error: res.error.message }, 502)
  return json(mapResults(rows, res.data as RowResult[]))
})
