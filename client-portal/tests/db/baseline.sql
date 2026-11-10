-- LOCAL TEST ONLY. An approximation of the remote Supabase schema, rebuilt from
-- "the original application schema" because the real one was created in the
-- dashboard and is not in version control. All records are FICTIONAL.

-- Supabase built-ins the migrations depend on
create role anon nologin;
create role authenticated nologin;
create schema auth;
create table auth.users (id uuid primary key, email text);
create function auth.uid() returns uuid language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                  (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'))::uuid
$$;
grant usage on schema public, auth to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;

-- Tables as documented
create table public.businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null, slug text not null unique,
  created_at timestamptz not null default now());
create table public.memberships (
  business_id uuid not null references public.businesses on delete cascade,
  user_id uuid not null references auth.users on delete cascade,
  role text not null default 'member' check (role in ('owner','member')),
  unique (business_id, user_id));
create table public.inquiries (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses,
  source_id text not null,
  created_at timestamptz not null default now(),
  name text not null, phone text not null, service text not null,
  preferred_time text not null, duration text not null,
  preferences text not null default '',
  status text not null default 'new' check (status in ('new','contacted','closed')),
  owner_notes text not null default '',
  unique (business_id, source_id));

-- FICTIONAL seed shaped like the four imported test calls
insert into auth.users values ('aaaaaaaa-0000-4000-8000-000000000001', 'fictional-member@example.test');
insert into public.businesses (id, name, slug) values
  ('d00c0c1d-7687-5992-bfbd-b69a214c566e', 'Example Studios', 'sample-studio');
insert into public.memberships values
  ('d00c0c1d-7687-5992-bfbd-b69a214c566e', 'aaaaaaaa-0000-4000-8000-000000000001', 'member');
insert into public.inquiries (business_id, source_id, created_at, name, phone, service, preferred_time, duration, preferences) values
  ('d00c0c1d-7687-5992-bfbd-b69a214c566e', 'fict-1', '2026-09-17T05:03:01Z', 'FICTIONAL Caller One', '+15555550101', 'recording', 'Friday at midnight', '2 hours', 'Studio A'),
  ('d00c0c1d-7687-5992-bfbd-b69a214c566e', 'fict-2', '2026-09-17T20:17:00Z', 'FICTIONAL Caller Two', '+15555550102', 'recording', 'to discuss', 'to discuss', ''),
  ('d00c0c1d-7687-5992-bfbd-b69a214c566e', 'fict-3', '2026-09-17T20:39:57Z', 'FICTIONAL Caller Three', '+15555550103', 'recording', 'Saturday 2pm', '3 hours', 'Own engineer'),
  ('d00c0c1d-7687-5992-bfbd-b69a214c566e', 'fict-4', '2026-09-17T20:47:34Z', 'FICTIONAL Caller Four', '+15555550104', 'recording', 'Sunday', '4 hours', '');
