-- SenseBand cloud schema (Supabase / Postgres 15+). Mirrors the edge SQLite tables in
-- docs/05_DATA_MODEL_AND_API.md so edge payloads upsert 1:1. Ids created on the edge are TEXT
-- (UUID strings; the patient id is whatever the band is linked to). Timestamps: UTC epoch ms.
--
-- Authority (docs/02): the EDGE is the source of truth for measurements; the CLOUD is the source of
-- truth for profile, care team, plans and doctor edits. Rows in the four "cloud-authoritative" tables
-- (patients, exercise_plans, recovery_cases, notes) get cloud_edited = true when a signed-in user
-- changes them; after that the device can no longer overwrite them and pulls the cloud version.

create table public.profiles (
  id uuid primary key references auth.users on delete cascade,
  name text, phone text, email text,
  role_default text not null default 'patient' check (role_default in ('patient', 'caregiver', 'doctor')),
  created_at timestamptz not null default now()
);

create table public.patients (
  id text primary key,
  name text, dob date, sex text, height_cm real, weight_kg real,
  conditions jsonb not null default '[]', medications jsonb not null default '[]',
  resting_hr real, emergency_contacts jsonb not null default '[]',
  created_at bigint, owner uuid references auth.users,
  cloud_edited boolean not null default false, updated_at timestamptz not null default now()
);

create table public.care_team (
  patient_id text not null references public.patients on delete cascade,
  user_id uuid not null references auth.users on delete cascade,
  role text not null check (role in ('patient', 'caregiver', 'doctor')),
  status text not null default 'active' check (status in ('invited', 'active', 'revoked')),
  created_at timestamptz not null default now(),
  primary key (patient_id, user_id)
);
create index care_team_user on public.care_team (user_id) where status = 'active';

create table public.invites (
  code text primary key,
  patient_id text not null references public.patients on delete cascade,
  role text not null check (role in ('caregiver', 'doctor')),
  created_by uuid not null references auth.users,
  expires_at timestamptz not null default now() + interval '7 days',
  used_by uuid references auth.users, used_at timestamptz
);

create table public.devices (
  id text primary key, patient_id text not null references public.patients on delete cascade,
  main_serial text, small_ble_name text, fw_versions jsonb, last_seen bigint,
  key_hash text not null unique
);

create table public.programs (
  id text primary key, patient_id text not null, type text not null, purpose text,
  start_ts bigint not null, planned_end_ts bigint, status text not null, settings_json jsonb
);
create table public.recovery_cases (
  id text primary key, patient_id text not null, program_id text not null, limb text, joint text, side text,
  injury_type text, injury_date date, cast_removed_date date, max_angle_limits_json jsonb,
  weight_bearing text, doctor_approved smallint not null default 0,
  cloud_edited boolean not null default false, updated_at timestamptz not null default now()
);
create table public.calibrations (
  id text primary key, patient_id text not null, program_id text, ts bigint, joint text, offsets_json jsonb
);
create table public.vitals_minute (
  patient_id text not null, minute_ts bigint not null,
  hr_ecg real, hr_ppg real, hr_expected real, spo2_min real, spo2_avg real, temp real,
  activity text, intensity real, posture text, rhythm text, quality_flags jsonb, ews integer,
  primary key (patient_id, minute_ts)
);
create table public.events (
  id text primary key, patient_id text not null, ts bigint not null,
  level text not null check (level in ('info', 'warning', 'critical')), kind text not null, message text,
  context_json jsonb, acknowledged_by text, ack_ts bigint, sms_sent integer default 0, status text,
  cancelled_ts bigint
);
create index events_patient_ts on public.events (patient_id, ts desc);
create table public.ecg_strips (
  id text primary key, patient_id text not null, event_id text, ts bigint, fs integer, storage_path text
);
create table public.assessments (
  id text primary key, patient_id text not null, program_id text, ts bigint, kind text, movement text,
  side text, min_angle real, max_angle real, rom real, grip_peak real, grip_mean real, unit text,
  pain_0_10 integer, phase text
);
create table public.exercise_plans (
  id text primary key, patient_id text not null, program_id text, version integer, created_by text,
  approved_by text, stage text, items_json jsonb,
  cloud_edited boolean not null default false, updated_at timestamptz not null default now()
);
create table public.exercise_sessions (
  id text primary key, patient_id text not null, program_id text, plan_id text, start_ts bigint, end_ts bigint,
  exercise_id text, reps_done integer, avg_peak_angle real, max_angle real, target_hit_pct real,
  too_fast_count integer, pain_before integer, pain_after integer, stopped_reason text
);
create table public.forecasts (
  id text primary key, patient_id text not null, program_id text, ts bigint, params_json jsonb,
  est_target_date text, ci_low text, ci_high text
);
create table public.notes (
  id text primary key, patient_id text not null, author_id uuid, author_name text, ts bigint, text text,
  cloud_edited boolean not null default false, updated_at timestamptz not null default now()
);
create index notes_patient on public.notes (patient_id, ts desc);

create table public.push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users on delete cascade default auth.uid(),
  endpoint text not null unique, p256dh text not null, auth text not null,
  created_at timestamptz not null default now()
);

-- ---- triggers: mark human edits, keep updated_at fresh, stamp doctor metadata -------------------
create function public.touch_authoritative() returns trigger language plpgsql as $$
begin
  if auth.uid() is not null and coalesce(current_setting('senseband.skip_touch', true), '') <> 'on' then
    new.cloud_edited := true;                  -- a signed-in human wrote this
  end if;
  new.updated_at := now();
  return new;
end $$;

create trigger patients_touch before insert or update on public.patients
  for each row execute function public.touch_authoritative();
create trigger plans_touch before insert or update on public.exercise_plans
  for each row execute function public.touch_authoritative();
create trigger cases_touch before insert or update on public.recovery_cases
  for each row execute function public.touch_authoritative();
create trigger notes_touch before insert or update on public.notes
  for each row execute function public.touch_authoritative();

create function public.stamp_user_rows() returns trigger language plpgsql security definer
  set search_path = public as $$
declare who text;
begin
  select name into who from public.profiles where id = auth.uid();
  if tg_table_name = 'notes' and auth.uid() is not null then
    new.author_id := auth.uid(); new.author_name := coalesce(who, 'Doctor');
    new.ts := coalesce(new.ts, (extract(epoch from now()) * 1000)::bigint);
    new.id := coalesce(new.id, gen_random_uuid()::text);
  elsif tg_table_name = 'exercise_plans' and auth.uid() is not null then
    new.created_by := 'doctor';
    if new.approved_by is not null then new.approved_by := coalesce(who, 'Doctor'); end if;
    new.id := coalesce(new.id, gen_random_uuid()::text);
  end if;
  return new;
end $$;
create trigger notes_stamp before insert on public.notes for each row execute function public.stamp_user_rows();
create trigger plans_stamp before insert or update on public.exercise_plans for each row execute function public.stamp_user_rows();

-- Realtime (Supabase): alerts and vitals reach caregiver/doctor apps instantly
do $$ begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.events, public.vitals_minute;
  end if;
end $$;
