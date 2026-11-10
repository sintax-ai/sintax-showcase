-- 002 - Let business members correct what the voice agent captured.
--
-- Additive only: no rows deleted, no columns dropped, existing values untouched.
-- Runs as one transaction: if any step fails, nothing is changed.
-- Requires 001_access.sql (already applied remotely).
--
-- What it does:
--   1. `original`   - a frozen copy of what the agent captured. Set by the database
--                     on insert and never changeable afterwards, by anyone.
--   2. `updated_at` - lets the portal refuse to overwrite an edit made elsewhere.
--   3. Format rules on the correctable fields, enforced by the database.
--   4. Update permission on the six correctable fields. business_id, source_id,
--      created_at, original and updated_at remain NOT editable by portal users.
begin;

-- 0. Duplicate protection the uploader relies on. Expected to exist already;
--    created only if missing. Without it, upload retries would insert copies.
do $$
begin
  if not exists (
    select 1 from pg_index i
    where i.indrelid = 'public.inquiries'::regclass and i.indisunique
      and (select array_agg(a.attname::text order by a.attname)
           from pg_attribute a
           where a.attrelid = i.indrelid and a.attnum = any(i.indkey))
          = array['business_id', 'source_id']
  ) then
    create unique index inquiries_business_source_key
      on public.inquiries (business_id, source_id);
  end if;
end $$;

-- 1. Captured snapshot. Backfilled BEFORE the trigger exists: the four imported
--    rows have never been edited, so their current values are the originals.
alter table public.inquiries add column if not exists original jsonb;
alter table public.inquiries add column if not exists updated_at timestamptz;

update public.inquiries
set original = jsonb_build_object(
      'name', name, 'phone', phone, 'service', service,
      'preferred_time', preferred_time, 'duration', duration,
      'preferences', preferences)
where original is null;

update public.inquiries set updated_at = created_at where updated_at is null;

alter table public.inquiries alter column original set not null;
alter table public.inquiries alter column updated_at set not null;
alter table public.inquiries alter column updated_at set default now();

create or replace function public.inquiries_protect_capture()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    -- Always the database's own copy; a client-supplied `original` is ignored.
    new.original := jsonb_build_object(
      'name', new.name, 'phone', new.phone, 'service', new.service,
      'preferred_time', new.preferred_time, 'duration', new.duration,
      'preferences', new.preferences);
    new.updated_at := coalesce(new.created_at, now());
  else
    new.original := old.original;  -- frozen, even for administrators
    new.updated_at := now();
  end if;
  return new;
end $$;

drop trigger if exists inquiries_protect_capture on public.inquiries;
create trigger inquiries_protect_capture
  before insert or update on public.inquiries
  for each row execute function public.inquiries_protect_capture();

-- 2. Format rules. Checked against existing rows too: if an imported row does not
--    fit, this whole migration stops and nothing is changed.
--    `service` is checked for shape, not against Example Studios's list, because future
--    client businesses will offer different services. The portal offers a fixed
--    menu per business so owners cannot mistype one.
do $$
declare
  rules text[][] := array[
    ['inquiries_name_valid',        'char_length(btrim(name)) between 1 and 200'],
    ['inquiries_phone_valid',       'phone ~ ''^\+1[0-9]{10}$'''],
    ['inquiries_service_valid',     'service ~ ''^[a-z][a-z0-9_]{0,49}$'''],
    ['inquiries_time_valid',        'char_length(btrim(preferred_time)) between 1 and 500'],
    ['inquiries_duration_valid',    'char_length(btrim(duration)) between 1 and 500'],
    ['inquiries_preferences_valid', 'char_length(coalesce(preferences, '''')) <= 2000'],
    ['inquiries_notes_valid',       'char_length(coalesce(owner_notes, '''')) <= 10000']
  ];
  r text[];
begin
  foreach r slice 1 in array rules loop
    if not exists (select 1 from pg_constraint
                   where conrelid = 'public.inquiries'::regclass and conname = r[1]) then
      execute format('alter table public.inquiries add constraint %I check (%s)', r[1], r[2]);
    end if;
  end loop;
end $$;

-- 3. Permission to correct captured fields. Adds to the existing
--    status/owner_notes grant; row access is still limited by the membership
--    policies from 001, which are unchanged.
grant update (name, phone, service, preferred_time, duration, preferences)
  on public.inquiries to authenticated;

commit;
