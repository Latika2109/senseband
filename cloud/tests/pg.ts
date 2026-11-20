// Real PostgreSQL (PGlite) with a minimal Supabase stand-in: auth.users, auth.uid(), roles.
import { PGlite } from '@electric-sql/pglite'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const MIGRATIONS = ['0001_schema.sql', '0002_security.sql']

export async function freshDb(): Promise<PGlite> {
  const db = new PGlite()
  await db.exec(`
    create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
    create schema auth;
    create table auth.users (id uuid primary key default gen_random_uuid(), phone text, email text);
    create function auth.uid() returns uuid language sql stable as $$
      select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
    grant usage on schema auth to anon, authenticated;
    grant execute on function auth.uid() to anon, authenticated;
  `)
  for (const m of MIGRATIONS) await db.exec(readFileSync(join(here, '../supabase/migrations', m), 'utf8'))
  return db
}

export async function addUser(db: PGlite, name: string, role = 'patient'): Promise<string> {
  const r = await db.query<{ id: string }>(`insert into auth.users (phone) values ($1) returning id`, [`+91${Math.floor(Math.random() * 1e9)}`])
  const id = r.rows[0]!.id
  await db.query(`insert into public.profiles (id, name, role_default) values ($1, $2, $3)`, [id, name, role])
  return id
}

/** Run `fn` as a signed-in user (role authenticated) or as the anon key (uid = null). */
export async function as<T>(db: PGlite, uid: string | null, fn: () => Promise<T>): Promise<T> {
  await db.exec(`set role ${uid ? 'authenticated' : 'anon'}; select set_config('request.jwt.claim.sub', '${uid ?? ''}', false);`)
  try { return await fn() } finally { await db.exec(`reset role; select set_config('request.jwt.claim.sub', '', false);`) }
}

export const q = <R = Record<string, unknown>>(db: PGlite, sql: string, params: unknown[] = []) => db.query<R>(sql, params).then((r) => r.rows)
export async function fails(p: Promise<unknown>): Promise<string> {
  try { await p } catch (e) { return String((e as Error).message) }
  throw new Error('expected the statement to fail but it succeeded')
}
