-- 004 - Tell the owner the moment a request lands (email now, text later).
--
-- Additive only: no rows deleted, no columns dropped, existing values untouched.
-- Runs as one transaction: if any step fails, nothing is changed.
-- Requires 003_transcripts.sql (already applied remotely).
--
-- What it does:
--   1. `businesses.notify_email` - where that business's alerts go. Empty means
--                     no alerts. Members can read it (existing select grant) but
--                     cannot change it: SINTAX sets it when onboarding a client.
--   2. `sintax_settings` - a tiny key/value table for the sender address, the
--                     reply-to address and the portal link used in the email.
--                     Portal users cannot read it. Alerts are sent from the
--                     subdomain mail.example.test (verified in Resend) so their
--                     reputation never touches support@example.test.
--   3. A trigger after every new inquiry queues one email through Resend, using
--                     pg_net (Supabase's asynchronous HTTP). The insert never
--                     waits: the request is queued inside the transaction and a
--                     background worker sends it after commit. If Resend is down
--                     the email is lost, not retried; the request itself is
--                     still saved and visible in the portal.
--   4. The Resend API key lives in Supabase Vault under the name
--                     `resend_api_key`. It is never in this file, in git, or in
--                     the portal. Set it with supabase/setup/004_alert_settings.sql.
--
-- Nothing is sent when notify_email is empty, the key is missing, or pg_net is
-- not installed (the practice database): the trigger simply returns. An alert
-- failure can never stop a request from being saved.
begin;

-- 1. Where alerts go
alter table public.businesses add column if not exists notify_email text;

do $$
begin
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.businesses'::regclass
                   and conname = 'businesses_notify_email_valid') then
    alter table public.businesses add constraint businesses_notify_email_valid check (
      notify_email is null
      or notify_email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$');
  end if;
end $$;

-- 2. Settings the email needs. Access revoked below: portal users cannot read it.
create table if not exists public.sintax_settings (
  key text primary key,
  value text not null,
  updated_at timestamptz not null default now()
);
alter table public.sintax_settings enable row level security;
-- Supabase grants new tables to anon/authenticated by default; take it back so
-- portal users get "permission denied", not an empty result they could later be
-- granted into by an accidental policy.
revoke all on table public.sintax_settings from public;
revoke all on table public.sintax_settings from anon, authenticated;

insert into public.sintax_settings (key, value) values
  ('alert_from', 'SINTAX <alerts@mail.example.test>'),
  ('alert_reply_to', 'support@example.test'),
  ('portal_url', 'http://localhost:4175')
on conflict (key) do nothing;

-- 3. The alert. security definer: runs with the database owner's rights so it
--    can read the Vault and call pg_net, whoever inserted the row.
create or replace function public.inquiries_alert_owner()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  to_addr  text;
  biz_name text;
  api_key  text;
  sender   text;
  reply_to text;
  portal   text;
  subject  text;
  body     text;
  phone    text;
  service  text;
begin
  select b.notify_email, b.name into to_addr, biz_name
  from public.businesses b where b.id = new.business_id;
  if to_addr is null then
    return new;
  end if;
  if not exists (select 1 from pg_extension where extname = 'pg_net') then
    raise notice 'pg_net is not installed: no alert sent for inquiry %', new.id;
    return new;
  end if;
  if not exists (select 1 from pg_namespace where nspname = 'vault') then
    raise notice 'Vault is not available: no alert sent for inquiry %', new.id;
    return new;
  end if;
  execute $q$select decrypted_secret from vault.decrypted_secrets
             where name = 'resend_api_key' order by created_at desc limit 1$q$
    into api_key;
  if api_key is null or api_key = '' then
    raise notice 'resend_api_key is not set in Vault: no alert sent for inquiry %', new.id;
    return new;
  end if;

  select s.value into sender from public.sintax_settings s where s.key = 'alert_from';
  select s.value into reply_to from public.sintax_settings s where s.key = 'alert_reply_to';
  select s.value into portal from public.sintax_settings s where s.key = 'portal_url';
  phone   := regexp_replace(new.phone, '^\+1(\d{3})(\d{3})(\d{4})$', '(\1) \2-\3');
  service := replace(new.service, '_', ' ');
  subject := format('New call: %s wants %s, %s', new.name, service, new.preferred_time);
  body := format(
    E'%s just took a call for %s.\n\n'
    || E'Name: %s\nPhone: %s\nService: %s\nRequested time: %s\nDuration: %s\nPreferences: %s\n\n'
    || E'This is a request, not a confirmed booking. Call them back to confirm.\n\n'
    || E'Open your requests: %s\n',
    'Your AI receptionist', biz_name,
    new.name, phone, service, new.preferred_time, new.duration,
    coalesce(nullif(new.preferences, ''), '(none)'),
    coalesce(portal, ''));

  execute $q$select net.http_post(
              url     := 'https://api.resend.com/emails',
              headers := $1,
              body    := $2)$q$
    using jsonb_build_object('Authorization', 'Bearer ' || api_key,
                             'Content-Type', 'application/json'),
          jsonb_build_object('from', coalesce(sender, 'SINTAX <alerts@mail.example.test>'),
                             'to', jsonb_build_array(to_addr),
                             'subject', subject,
                             'text', body)
            || case when reply_to is null then '{}'::jsonb
                    else jsonb_build_object('reply_to', reply_to) end;
  return new;
exception when others then
  -- An alert problem must never stop a request from being saved.
  raise warning 'alert not sent for inquiry %: %', new.id, sqlerrm;
  return new;
end $$;

revoke execute on function public.inquiries_alert_owner() from public;
revoke execute on function public.inquiries_alert_owner() from anon, authenticated;

drop trigger if exists inquiries_alert_owner on public.inquiries;
create trigger inquiries_alert_owner
  after insert on public.inquiries
  for each row execute function public.inquiries_alert_owner();

-- 4. pg_net, where available (Supabase has it). Harmless elsewhere.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_net')
     and not exists (select 1 from pg_extension where extname = 'pg_net') then
    begin
      create extension if not exists pg_net;
    exception when others then
      raise notice 'pg_net could not be enabled from here (%). In Supabase: Database > Extensions > enable pg_net, then run this file again.', sqlerrm;
    end;
  end if;
  if not exists (select 1 from pg_extension where extname = 'pg_net') then
    raise notice 'pg_net is not available here: requests are saved, alerts are not sent.';
  end if;
end $$;

commit;
