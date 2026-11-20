// Pure helpers for the `ingest` Edge Function (unit-tested with vitest; no Deno globals here).

export interface OutboxRow { id: number; table: string; row_id: string; op: string; payload: Record<string, unknown> }
export interface RowResult { row_id: string; status: 'ok' | 'skipped' | 'error'; error?: string }
export interface Strip { rowId: string; path: string; bytes: Uint8Array }

export const MAX_ROWS = 500
export const MAX_BODY_BYTES = 2_000_000

export function parseBody(body: unknown): { rows: OutboxRow[] } | { error: string } {
  if (typeof body !== 'object' || body === null || !Array.isArray((body as { rows?: unknown }).rows)) return { error: 'body must be {"rows": [...]}' }
  const rows = (body as { rows: unknown[] }).rows
  if (rows.length > MAX_ROWS) return { error: `at most ${MAX_ROWS} rows per request` }
  for (const r of rows) {
    const x = r as Partial<OutboxRow>
    if (typeof x?.id !== 'number' || typeof x.table !== 'string' || typeof x.row_id !== 'string' || typeof x.payload !== 'object' || x.payload === null) {
      return { error: 'every row needs numeric id, table, row_id and an object payload' }
    }
  }
  return { rows: rows as OutboxRow[] }
}

export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

/** Pull ECG sample blobs out of the payloads (they go to Storage, not the table) and point the row at the path. */
export function extractStrips(rows: OutboxRow[], patientId: string): { rows: OutboxRow[]; strips: Strip[] } {
  const strips: Strip[] = []
  const out = rows.map((r) => {
    if (r.table !== 'ecg_strips' || typeof r.payload.samples_b64 !== 'string') return r
    const id = String(r.payload.id ?? r.row_id).replace(/[^A-Za-z0-9_-]/g, '')
    const path = `${patientId}/${id}.f32`
    strips.push({ rowId: r.row_id, path, bytes: b64ToBytes(r.payload.samples_b64) })
    const { samples_b64: _drop, ...rest } = r.payload
    void _drop
    return { ...r, payload: { ...rest, storage_path: path } }
  })
  return { rows: out, strips }
}

/** Map the database's per-row results back to outbox ids for the band. "skipped" counts as accepted (cloud wins). */
export function mapResults(sent: OutboxRow[], results: RowResult[]): { accepted: number[]; rejected: { id: number; error: string }[] } {
  const byRow = new Map(results.map((r) => [r.row_id, r]))
  const accepted: number[] = []
  const rejected: { id: number; error: string }[] = []
  for (const r of sent) {
    const res = byRow.get(r.row_id)
    if (res && (res.status === 'ok' || res.status === 'skipped')) accepted.push(r.id)
    else rejected.push({ id: r.id, error: res?.error ?? 'no result for row' })
  }
  return { accepted, rejected }
}

export function bearer(header: string | null): string | null {
  const m = /^Bearer\s+(\S{16,200})$/.exec(header ?? '')
  return m?.[1] ?? null
}
