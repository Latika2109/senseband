import { expect, it } from 'vitest'
import { freshDb } from './pg'

it('migrations apply on real Postgres', async () => {
  const db = await freshDb()
  const r = await db.query<{ n: number }>(`select count(*)::int as n from information_schema.tables where table_schema='public'`)
  expect(r.rows[0]!.n).toBeGreaterThan(15)
})
