-- 007: account-related business preferences and SINTAX review requests.
-- Additive. Existing memberships, recipient addresses, calls and inquiries stay intact.
begin;

create schema if not exists sintax_private;
revoke all on schema sintax_private from public, anon, authenticated;

alter table public.businesses add column if not exists request_alerts_enabled boolean not null default true;
alter table public.businesses add column if not exists settings_updated_at timestamptz not null default now();

create or replace function sintax_private.stamp_business_settings()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
  if new.notify_email is distinct from old.notify_email
     or new.request_alerts_enabled is distinct from old.request_alerts_enabled then
    new.settings_updated_at := greatest(clock_timestamp(), old.settings_updated_at + interval '1 microsecond');
  end if;
  return new;
end $$;
revoke all on function sintax_private.stamp_business_settings() from public, anon, authenticated;
drop trigger if exists businesses_stamp_settings on public.businesses;
create trigger businesses_stamp_settings before update of notify_email,request_alerts_enabled
  on public.businesses for each row execute function sintax_private.stamp_business_settings();

grant update(notify_email,request_alerts_enabled) on public.businesses to authenticated;
drop policy if exists businesses_update_owner_preferences on public.businesses;
create policy businesses_update_owner_preferences on public.businesses for update to authenticated
using (id in (select business_id from public.memberships where user_id=(select auth.uid()) and role='owner'))
with check (id in (select business_id from public.memberships where user_id=(select auth.uid()) and role='owner'));

-- Shared by actual delivery and its local, side-effect-free enabled/disabled checks.
create or replace function sintax_private.customer_request_alert_recipient(business uuid)
returns text language sql stable security invoker set search_path = '' as $$
  select case when b.request_alerts_enabled then b.notify_email else null end
  from public.businesses b where b.id=business
$$;
revoke all on function sintax_private.customer_request_alert_recipient(uuid) from public, anon, authenticated;

create table if not exists public.receptionist_change_requests (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id),
  requested_by uuid not null default auth.uid() references auth.users(id),
  category text not null check(category in ('hours','services','pricing','voice','instructions','other')),
  description text not null check(char_length(description) between 1 and 4000 and description ~ '[^[:space:]]'),
  status text not null default 'submitted' check(status in ('submitted','reviewing','completed','declined')),
  created_at timestamptz not null default now()
);
create index if not exists receptionist_change_requests_business_created
  on public.receptionist_change_requests(business_id,created_at desc);
create index if not exists receptionist_change_requests_requester
  on public.receptionist_change_requests(requested_by);
alter table public.receptionist_change_requests enable row level security;
revoke all on public.receptionist_change_requests from public, anon, authenticated;
grant select on public.receptionist_change_requests to authenticated;
grant insert(id,business_id,category,description) on public.receptionist_change_requests to authenticated;
drop policy if exists receptionist_changes_read_member on public.receptionist_change_requests;
create policy receptionist_changes_read_member on public.receptionist_change_requests for select to authenticated
using (business_id in (select business_id from public.memberships where user_id=(select auth.uid())));
drop policy if exists receptionist_changes_insert_member on public.receptionist_change_requests;
create policy receptionist_changes_insert_member on public.receptionist_change_requests for insert to authenticated
with check (requested_by=(select auth.uid())
  and business_id in (select business_id from public.memberships where user_id=(select auth.uid())));

insert into public.sintax_settings(key,value) values ('support_email','support@example.test') on conflict(key) do nothing;

create or replace function sintax_private.receptionist_change_notify_sintax()
returns trigger language plpgsql security definer set search_path = '' as $$
declare
  recipient text; sender text; reply_to text; api_key text; business_name text; portal text;
begin
  if not exists(select 1 from pg_extension where extname='pg_net')
     or not exists(select 1 from pg_namespace where nspname='vault') then return new; end if;
  select value into recipient from public.sintax_settings where key='support_email';
  if recipient is null or recipient='' then return new; end if;
  execute $q$select decrypted_secret from vault.decrypted_secrets where name='resend_api_key' order by created_at desc limit 1$q$ into api_key;
  if api_key is null or api_key='' then return new; end if;
  select value into sender from public.sintax_settings where key='alert_from';
  select value into reply_to from public.sintax_settings where key='alert_reply_to';
  select value into portal from public.sintax_settings where key='portal_url';
  select name into business_name from public.businesses where id=new.business_id;
  execute $q$select net.http_post(url:='https://api.resend.com/emails',headers:=$1,body:=$2)$q$
    using jsonb_build_object('Authorization','Bearer '||api_key,'Content-Type','application/json'),
      jsonb_build_object('from',coalesce(sender,'SINTAX <alerts@mail.example.test>'),
        'to',jsonb_build_array(recipient),
        'subject',format('Receptionist change request: %s',business_name),
        'text',format(E'%s submitted a receptionist change for SINTAX review.\n\nCategory: %s\nRequest: %s\n\nReference: %s\nRequester ID: %s\n\nThis has not changed the receptionist. Review the saved request in Supabase before applying any change.\nPortal: %s\n',
          business_name,new.category,new.description,new.id,new.requested_by,coalesce(portal,'')))
        || case when reply_to is null then '{}'::jsonb else jsonb_build_object('reply_to',reply_to) end;
  return new;
exception when others then
  raise warning 'SINTAX change notification not queued for request %: %',new.id,sqlerrm;
  return new;
end $$;
revoke all on function sintax_private.receptionist_change_notify_sintax() from public, anon, authenticated;
drop trigger if exists receptionist_change_notify_sintax on public.receptionist_change_requests;
create trigger receptionist_change_notify_sintax after insert on public.receptionist_change_requests
  for each row execute function sintax_private.receptionist_change_notify_sintax();

-- The existing inquiry alert function is replaced below with only its recipient
-- selection changed to honor request_alerts_enabled. Its delivery behavior stays intact.

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
  select sintax_private.customer_request_alert_recipient(b.id), b.name into to_addr, biz_name
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

revoke execute on function public.inquiries_alert_owner() from public, anon, authenticated;
commit;
