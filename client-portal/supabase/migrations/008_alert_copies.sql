-- 008: Optional administrative copy recipient, scoped to one business.
-- Configure public.sintax_settings key alert_cc:<business UUID> separately.
-- Existing owners still control their primary address and disable all alerts.
begin;
create or replace function sintax_private.customer_request_alert_addresses(business uuid)
returns jsonb language plpgsql stable security invoker set search_path = '' as $$
declare
  primary_address text;
  copy_address text;
  recipients jsonb;
begin
  primary_address := sintax_private.customer_request_alert_recipient(business);
  if primary_address is null or btrim(primary_address) = '' then return '{}'::jsonb; end if;
  recipients := jsonb_build_object('to',jsonb_build_array(primary_address));
  select btrim(value) into copy_address from public.sintax_settings where key='alert_cc:'||business;
  if copy_address ~ '^[^@[:space:],;<>]+@[^@[:space:],;<>]+[.][^@[:space:],;<>]+$'
     and lower(copy_address) <> lower(btrim(primary_address)) then
    recipients := recipients || jsonb_build_object('cc',jsonb_build_array(copy_address));
  end if;
  return recipients;
end $$;
revoke all on function sintax_private.customer_request_alert_addresses(uuid) from public, anon, authenticated;

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
                             'subject', subject,
                             'text', body)
            || sintax_private.customer_request_alert_addresses(new.business_id)
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
