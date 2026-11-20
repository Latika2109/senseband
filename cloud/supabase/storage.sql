-- Supabase-only (needs the `storage` schema). Not run in CI. Private bucket for ECG strips + RLS:
-- readable by the patient's active care team; written only by the ingest function (service role).
insert into storage.buckets (id, name, public) values ('ecg-strips', 'ecg-strips', false) on conflict do nothing;
create policy "care team reads ecg strips" on storage.objects for select to authenticated
  using (bucket_id = 'ecg-strips' and public.is_care((storage.foldername(name))[1]));
