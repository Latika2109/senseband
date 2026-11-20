# SenseBand cloud (Supabase)

```
supabase/migrations/0001_schema.sql    tables mirroring the edge SQLite schema (+ triggers, realtime)
supabase/migrations/0002_security.sql  RLS, grants, RPC (invites, ingest_batch, pull_changes, erasure)
supabase/storage.sql                   private ECG-strip bucket (Supabase only)
supabase/functions/ingest|pull|notify  Edge Functions (Deno); pure logic in functions/_shared
tests/                                 vitest: RLS + ingest on REAL PostgreSQL (PGlite), helper unit tests
```

Run the tests: `cd cloud && npm i && npm test`

## What is and is not verified
Verified in CI on real PostgreSQL 16 (PGlite with a stand-in for `auth.uid()`): schema, RLS (a doctor
sees only invited patients), invites, revocation, device-key ingest (idempotent, one patient only),
pull cursor, erasure, grants.
**Not verified** (needs a Supabase project): Auth phone OTP provider setup, Realtime delivery, Storage
policies (`storage.sql`), the Deno Edge Function entrypoints, web push and SMS providers.

## Deploy checklist
1. `supabase db push` (runs both migrations); run `storage.sql` once.
2. Auth > Providers: enable Phone (OTP via your SMS provider).
3. `supabase functions deploy ingest pull --no-verify-jwt` (the band authenticates with its device key, not a user JWT) and `supabase functions deploy notify --no-verify-jwt`; secrets: `WEBHOOK_SECRET`, `VAPID_PUBLIC_KEY`,
   `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, optional `SMS_PROVIDER` (`twilio`|`msg91`) + provider keys.
4. Database > Webhooks: `public.events` INSERT and UPDATE -> `notify` with `Authorization: Bearer $WEBHOOK_SECRET`.
5. Link a band: as the patient call `select register_device('<patient id>', '<serial>')`, put the returned
   key in the band's `cloud.device_key`, set `cloud.url` to `https://<project>.supabase.co/functions/v1`,
   and make the band's `patient.id` the same patient id.

## Authority rules (docs/02)
Measurements: edge wins (idempotent upserts, ids born on the band). Profile, plans, doctor limits, notes:
cloud wins once a signed-in person edited them (`cloud_edited`); the band then pulls the cloud version.
