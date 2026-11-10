-- Calls exist independently of requests; audio is private and retained until
-- explicitly removed by a server-side retention process (none enabled).
-- Apply after 005. Additive and safe to re-run; no historical links are guessed.
begin;
create table if not exists public.calls (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id),
  session_id text not null,
  room_id text,
  mode text not null check(mode in ('demo','pilot')),
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  duration_seconds double precision check(duration_seconds >= 0 and duration_seconds < 'Infinity'::double precision),
  recording_status text not null default 'pending' check(recording_status in ('pending','available','unavailable')),
  recording_path text,
  recording_expires_at timestamptz,
  is_trashed boolean not null default false,
  updated_at timestamptz not null default clock_timestamp(),
  unique(business_id,session_id),
  unique(business_id,id),
  check(recording_path is null or recording_path = business_id::text||'/'||id::text||'/audio.ogg'),
  check(recording_status <> 'available' or recording_path is not null)
);
create index if not exists calls_business_started_idx on public.calls(business_id,started_at desc,id);
alter table public.calls enable row level security;
revoke all on public.calls from public,anon,authenticated;
grant select on public.calls to authenticated;
grant update(is_trashed) on public.calls to authenticated;
grant all on public.calls to service_role;
drop policy if exists calls_read_member on public.calls;
create policy calls_read_member on public.calls for select to authenticated
  using(business_id in (select business_id from public.memberships where user_id=(select auth.uid())));
drop policy if exists calls_update_member on public.calls;
create policy calls_update_member on public.calls for update to authenticated
  using(business_id in (select business_id from public.memberships where user_id=(select auth.uid())))
  with check(business_id in (select business_id from public.memberships where user_id=(select auth.uid())));
create or replace function public.calls_touch_updated_at() returns trigger
language plpgsql set search_path='' as $$
begin
  new.updated_at := clock_timestamp();
  return new;
end $$;
revoke all on function public.calls_touch_updated_at() from public,anon,authenticated;
drop trigger if exists calls_touch_updated_at on public.calls;
create trigger calls_touch_updated_at before update on public.calls
  for each row execute function public.calls_touch_updated_at();
alter table public.inquiries add column if not exists call_id uuid;
do $$ begin
  if not exists(select 1 from pg_constraint where conrelid='public.inquiries'::regclass and conname='inquiries_call_business_fk') then
    alter table public.inquiries add constraint inquiries_call_business_fk
      foreign key(business_id,call_id) references public.calls(business_id,id);
  end if;
end $$;
create index if not exists inquiries_call_idx on public.inquiries(business_id,call_id) where call_id is not null;
-- Existing column grants deliberately do not allow clients to alter call_id.
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('call-recordings','call-recordings',false,52428800,array['audio/ogg','application/ogg'])
on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit,allowed_mime_types=excluded.allowed_mime_types;
drop policy if exists call_recordings_read_member on storage.objects;
create policy call_recordings_read_member on storage.objects for select to authenticated
using (
  bucket_id='call-recordings' and exists(
    select 1 from public.calls c where c.recording_path=storage.objects.name
      and c.recording_status='available'
      and (c.recording_expires_at is null or c.recording_expires_at>now())
      -- Explicit membership as well as calls RLS; no object owner shortcut.
      and c.business_id in(select business_id from public.memberships where user_id=(select auth.uid()))
  )
);
notify pgrst,'reload schema';
commit;
