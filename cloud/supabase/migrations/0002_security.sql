-- Row Level Security, grants and the RPC functions the apps and the band use.
-- Principle: nobody gets a table grant they do not need; every read is gated by an ACTIVE care_team row;
-- every write path to measurements goes through ingest_batch() with a per-band device key.

-- ---- helpers ------------------------------------------------------------------------------------
create function public.is_care(pid text, roles text[] default null) returns boolean
language sql security definer stable set search_path = public as $$
  select exists (select 1 from public.care_team ct
    where ct.patient_id = pid and ct.user_id = auth.uid() and ct.status = 'active'
      and (roles is null or ct.role = any (roles)))
$$;

-- ---- enable RLS everywhere ----------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.patients enable row level security;
alter table public.care_team enable row level security;
alter table public.invites enable row level security;
alter table public.devices enable row level security;
alter table public.programs enable row level security;
alter table public.recovery_cases enable row level security;
alter table public.calibrations enable row level security;
alter table public.vitals_minute enable row level security;
alter table public.events enable row level security;
alter table public.ecg_strips enable row level security;
alter table public.assessments enable row level security;
alter table public.exercise_plans enable row level security;
alter table public.exercise_sessions enable row level security;
alter table public.forecasts enable row level security;
alter table public.notes enable row level security;
alter table public.push_subscriptions enable row level security;

-- ---- grants: start from nothing ------------------------------------------------------------------
revoke all on all tables in schema public from anon, authenticated;
grant usage on schema public to anon, authenticated;

grant select, insert, update on public.profiles to authenticated;
grant select, update on public.patients to authenticated;
grant select on public.care_team, public.invites, public.programs, public.calibrations,
  public.vitals_minute, public.events, public.ecg_strips, public.assessments,
  public.exercise_sessions, public.forecasts to authenticated;
grant select, update on public.recovery_cases to authenticated;
grant select, insert, update on public.exercise_plans to authenticated;
grant select, insert on public.notes to authenticated;
grant select (id, patient_id, main_serial, small_ble_name, fw_versions, last_seen) on public.devices to authenticated;
grant select, insert, delete on public.push_subscriptions to authenticated;

-- ---- policies -----------------------------------------------------------------------------------
create policy profiles_read on public.profiles for select to authenticated using (
  id = auth.uid() or exists (
    select 1 from public.care_team a join public.care_team b on a.patient_id = b.patient_id
    where a.user_id = auth.uid() and a.status = 'active' and b.user_id = profiles.id and b.status = 'active'));
create policy profiles_insert on public.profiles for insert to authenticated with check (id = auth.uid());
create policy profiles_update on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

create policy patients_read on public.patients for select to authenticated using (public.is_care(id));
create policy patients_update on public.patients for update to authenticated
  using (public.is_care(id, array['patient', 'caregiver'])) with check (public.is_care(id, array['patient', 'caregiver']));

create policy care_team_read on public.care_team for select to authenticated
  using (user_id = auth.uid() or public.is_care(patient_id));
create policy invites_read on public.invites for select to authenticated
  using (public.is_care(patient_id, array['patient', 'caregiver']));

-- measurement tables: read-only for people (the band writes through ingest_batch)
create policy programs_read on public.programs for select to authenticated using (public.is_care(patient_id));
create policy calibrations_read on public.calibrations for select to authenticated using (public.is_care(patient_id));
create policy vitals_read on public.vitals_minute for select to authenticated using (public.is_care(patient_id));
create policy events_read on public.events for select to authenticated using (public.is_care(patient_id));
create policy strips_read on public.ecg_strips for select to authenticated using (public.is_care(patient_id));
create policy assessments_read on public.assessments for select to authenticated using (public.is_care(patient_id));
create policy sessions_read on public.exercise_sessions for select to authenticated using (public.is_care(patient_id));
create policy forecasts_read on public.forecasts for select to authenticated using (public.is_care(patient_id));
create policy devices_read on public.devices for select to authenticated
  using (public.is_care(patient_id, array['patient', 'caregiver']));

-- doctor-owned: plans, limits, notes
create policy plans_read on public.exercise_plans for select to authenticated using (public.is_care(patient_id));
create policy plans_write on public.exercise_plans for insert to authenticated
  with check (public.is_care(patient_id, array['doctor']));
create policy plans_edit on public.exercise_plans for update to authenticated
  using (public.is_care(patient_id, array['doctor'])) with check (public.is_care(patient_id, array['doctor']));
create policy cases_read on public.recovery_cases for select to authenticated using (public.is_care(patient_id));
create policy cases_edit on public.recovery_cases for update to authenticated
  using (public.is_care(patient_id, array['doctor'])) with check (public.is_care(patient_id, array['doctor']));
create policy notes_read on public.notes for select to authenticated using (public.is_care(patient_id));
create policy notes_write on public.notes for insert to authenticated
  with check (public.is_care(patient_id, array['doctor']));

create policy push_own on public.push_subscriptions for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- ---- RPC for signed-in users ---------------------------------------------------------------------
create function public.create_patient(p_id text, p_name text, p_role text default 'patient') returns public.patients
language plpgsql security definer set search_path = public as $$
declare row public.patients;
begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '28000'; end if;
  if p_role not in ('patient', 'caregiver') then raise exception 'role must be patient or caregiver'; end if;
  if coalesce(trim(p_id), '') = '' or coalesce(trim(p_name), '') = '' then raise exception 'id and name required'; end if;
  perform set_config('senseband.skip_touch', 'on', true);   -- creating the row is not a "cloud edit"
  insert into public.patients (id, name, owner, created_at) values (p_id, p_name, auth.uid(), (extract(epoch from now()) * 1000)::bigint)
    returning * into row;
  insert into public.care_team (patient_id, user_id, role, status) values (p_id, auth.uid(), p_role, 'active');
  return row;
end $$;

create function public.create_invite(p_patient text, p_role text) returns text
language plpgsql security definer set search_path = public as $$
declare c text;
begin
  if not public.is_care(p_patient, array['patient', 'caregiver']) then raise exception 'not allowed' using errcode = '42501'; end if;
  if p_role not in ('caregiver', 'doctor') then raise exception 'role must be caregiver or doctor'; end if;
  c := upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8));
  insert into public.invites (code, patient_id, role, created_by) values (c, p_patient, p_role, auth.uid());
  return c;
end $$;

create function public.accept_invite(p_code text) returns public.care_team
language plpgsql security definer set search_path = public as $$
declare inv public.invites; row public.care_team;
begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '28000'; end if;
  select * into inv from public.invites where code = upper(trim(p_code)) for update;
  if not found then raise exception 'invalid invite code'; end if;
  if inv.used_at is not null then raise exception 'invite already used'; end if;
  if inv.expires_at < now() then raise exception 'invite expired'; end if;
  insert into public.care_team (patient_id, user_id, role, status) values (inv.patient_id, auth.uid(), inv.role, 'active')
    on conflict (patient_id, user_id) do update set role = excluded.role, status = 'active'
    returning * into row;
  update public.invites set used_by = auth.uid(), used_at = now() where code = inv.code;
  return row;
end $$;

create function public.revoke_access(p_patient text, p_user uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  -- the patient/caregiver can revoke anyone else; anyone can remove themselves
  if p_user <> auth.uid() and not public.is_care(p_patient, array['patient', 'caregiver']) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  if exists (select 1 from public.care_team where patient_id = p_patient and user_id = p_user and role = 'patient')
     and p_user <> auth.uid() then
    raise exception 'the patient cannot be revoked by others';
  end if;
  update public.care_team set status = 'revoked' where patient_id = p_patient and user_id = p_user;
end $$;

create function public.ack_event(p_event text) returns public.events
language plpgsql security definer set search_path = public as $$
declare ev public.events; who text;
begin
  select * into ev from public.events where id = p_event;
  if not found or not public.is_care(ev.patient_id) then raise exception 'not allowed' using errcode = '42501'; end if;
  select coalesce(name, 'user') into who from public.profiles where id = auth.uid();
  update public.events set acknowledged_by = who, ack_ts = (extract(epoch from now()) * 1000)::bigint
    where id = p_event returning * into ev;
  return ev;
end $$;

create function public.register_device(p_patient text, p_serial text default null) returns text
language plpgsql security definer set search_path = public as $$
declare k text;
begin
  if not public.is_care(p_patient, array['patient', 'caregiver']) then raise exception 'not allowed' using errcode = '42501'; end if;
  k := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
  insert into public.devices (id, patient_id, main_serial, key_hash)
    values (gen_random_uuid()::text, p_patient, p_serial, encode(sha256(convert_to(k, 'utf8')), 'hex'));
  return k;                       -- shown once; only its hash is stored
end $$;

create function public.delete_patient_data(p_patient text) returns void
language plpgsql security definer set search_path = public as $$
begin   -- DPDP Act 2023: erasure. Only the patient (not a caregiver or doctor) may erase everything.
  if not public.is_care(p_patient, array['patient']) then raise exception 'only the patient can delete all data' using errcode = '42501'; end if;
  delete from public.vitals_minute where patient_id = p_patient;
  delete from public.events where patient_id = p_patient;
  delete from public.ecg_strips where patient_id = p_patient;
  delete from public.assessments where patient_id = p_patient;
  delete from public.exercise_sessions where patient_id = p_patient;
  delete from public.exercise_plans where patient_id = p_patient;
  delete from public.forecasts where patient_id = p_patient;
  delete from public.calibrations where patient_id = p_patient;
  delete from public.recovery_cases where patient_id = p_patient;
  delete from public.programs where patient_id = p_patient;
  delete from public.notes where patient_id = p_patient;
  delete from public.patients where id = p_patient;   -- cascades care_team, invites, devices
end $$;


-- ---- triage view for the doctor / caregiver list (RLS applies: security_invoker) -----------------
create view public.patient_overview with (security_invoker = true) as
select p.id, p.name,
  coalesce((select max(case e.level when 'critical' then 3 when 'warning' then 2 else 1 end) from public.events e
    where e.patient_id = p.id and e.acknowledged_by is null and e.status is distinct from 'cancelled'), 0) as open_severity,
  (select count(*) from public.events e where e.patient_id = p.id and e.acknowledged_by is null
    and e.level <> 'info' and e.status is distinct from 'cancelled') as open_alerts,
  (select max(e.ts) from public.events e where e.patient_id = p.id) as last_event_ts,
  (select row_to_json(v) from (select m.minute_ts, m.hr_ecg, m.hr_ppg, m.spo2_avg, m.temp, m.activity, m.rhythm
    from public.vitals_minute m where m.patient_id = p.id order by m.minute_ts desc limit 1) v) as last_vitals
from public.patients p;
grant select on public.patient_overview to authenticated;

-- ---- band-facing RPC: authenticated by a per-device key, not by a user ---------------------------
create function public.device_for_key(p_key text) returns public.devices
language plpgsql security definer set search_path = public as $$
declare d public.devices;
begin
  select * into d from public.devices where key_hash = encode(sha256(convert_to(coalesce(p_key, ''), 'utf8')), 'hex');
  if not found then raise exception 'invalid device key' using errcode = '28000'; end if;
  return d;
end $$;

create function public.ingest_batch(p_device_key text, p_rows jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  d public.devices; r jsonb; tbl text; pay jsonb; cols text; sets text; cond text; pk text;
  owner_pid text; out jsonb := '[]'::jsonb; n int; rid text;
  allowed text[] := array['patients','programs','recovery_cases','calibrations','vitals_minute','events',
                          'ecg_strips','assessments','exercise_plans','exercise_sessions','forecasts','notes'];
  authoritative text[] := array['patients','recovery_cases','exercise_plans','notes'];
begin
  d := public.device_for_key(p_device_key);
  update public.devices set last_seen = (extract(epoch from now()) * 1000)::bigint where id = d.id;
  for r in select * from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) loop
    rid := r ->> 'row_id';
    begin
      tbl := r ->> 'table';
      if tbl is null or not (tbl = any (allowed)) then raise exception 'table not allowed: %', coalesce(tbl, 'null'); end if;
      pay := (r -> 'payload') - 'samples_b64';
      if jsonb_typeof(pay) is distinct from 'object' then raise exception 'payload must be an object'; end if;
      if tbl = 'patients' then
        if pay ->> 'id' is distinct from d.patient_id then raise exception 'device may only write its own patient'; end if;
        pay := pay - 'owner' - 'cloud_edited' - 'updated_at';
      else
        if pay ? 'patient_id' and pay ->> 'patient_id' <> d.patient_id then raise exception 'device may only write its own patient'; end if;
        pay := jsonb_set(pay - 'cloud_edited' - 'updated_at', '{patient_id}', to_jsonb(d.patient_id));
      end if;
      pk := case tbl when 'vitals_minute' then 'patient_id, minute_ts' else 'id' end;
      if tbl not in ('vitals_minute', 'patients') then   -- never let a device touch another patient's row id
        execute format('select patient_id from public.%I where id = $1', tbl) into owner_pid using pay ->> 'id';
        if owner_pid is not null and owner_pid <> d.patient_id then raise exception 'row belongs to another patient'; end if;
      end if;
      select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position) into cols
        from information_schema.columns c where c.table_schema = 'public' and c.table_name = tbl and pay ? c.column_name;
      select string_agg(format('%1$I = excluded.%1$I', c.column_name), ', ') into sets
        from information_schema.columns c where c.table_schema = 'public' and c.table_name = tbl and pay ? c.column_name
         and c.column_name not in ('id', 'patient_id', 'minute_ts');
      cond := case when tbl = any (authoritative) then 'not t.cloud_edited' else 'true' end;
      if sets is null then
        execute format('insert into public.%1$I as t (%2$s) select %2$s from jsonb_populate_record(null::public.%1$I, $1) on conflict (%3$s) do nothing', tbl, cols, pk) using pay;
        out := out || jsonb_build_object('row_id', rid, 'status', 'ok');
      else
        execute format('insert into public.%1$I as t (%2$s) select %2$s from jsonb_populate_record(null::public.%1$I, $1) on conflict (%3$s) do update set %4$s where %5$s',
                       tbl, cols, pk, sets, cond) using pay;
        get diagnostics n = row_count;
        out := out || jsonb_build_object('row_id', rid, 'status', case when n = 0 then 'skipped' else 'ok' end);
      end if;
    exception when others then
      out := out || jsonb_build_object('row_id', rid, 'status', 'error', 'error', sqlerrm);
    end;
  end loop;
  return out;
end $$;

create function public.pull_changes(p_device_key text, p_since timestamptz default 'epoch') returns jsonb
language plpgsql security definer set search_path = public as $$
declare d public.devices; mx timestamptz; res jsonb;
begin
  d := public.device_for_key(p_device_key);
  select jsonb_build_object(
    'patients', coalesce((select jsonb_agg(to_jsonb(x) - 'owner' - 'cloud_edited') from public.patients x
                          where x.id = d.patient_id and x.cloud_edited and x.updated_at > p_since), '[]'),
    'exercise_plans', coalesce((select jsonb_agg(to_jsonb(x) - 'cloud_edited') from public.exercise_plans x
                          where x.patient_id = d.patient_id and x.cloud_edited and x.updated_at > p_since), '[]'),
    'recovery_cases', coalesce((select jsonb_agg(to_jsonb(x) - 'cloud_edited') from public.recovery_cases x
                          where x.patient_id = d.patient_id and x.cloud_edited and x.updated_at > p_since), '[]'),
    'notes', coalesce((select jsonb_agg(to_jsonb(x) - 'cloud_edited') from public.notes x
                          where x.patient_id = d.patient_id and x.cloud_edited and x.updated_at > p_since), '[]'))
  into res;
  select greatest(
    (select max(updated_at) from public.patients where id = d.patient_id and cloud_edited and updated_at > p_since),
    (select max(updated_at) from public.exercise_plans where patient_id = d.patient_id and cloud_edited and updated_at > p_since),
    (select max(updated_at) from public.recovery_cases where patient_id = d.patient_id and cloud_edited and updated_at > p_since),
    (select max(updated_at) from public.notes where patient_id = d.patient_id and cloud_edited and updated_at > p_since)) into mx;
  return res || jsonb_build_object('cursor', coalesce(mx, p_since)::text);
end $$;

-- ---- function privileges (LAST: Postgres grants EXECUTE to PUBLIC on every new function) --------------
revoke execute on all functions in schema public from public, anon, authenticated;
grant execute on function public.is_care(text, text[]) to authenticated;
grant execute on function public.create_patient(text, text, text), public.create_invite(text, text),
  public.accept_invite(text), public.revoke_access(text, uuid), public.ack_event(text),
  public.register_device(text, text), public.delete_patient_data(text) to authenticated;
-- the band uses the anon key + its device key; device_for_key stays internal
grant execute on function public.ingest_batch(text, jsonb), public.pull_changes(text, timestamptz) to anon, authenticated;

-- Edge Functions run with the service role
grant execute on function public.device_for_key(text), public.ingest_batch(text, jsonb),
  public.pull_changes(text, timestamptz) to service_role;
