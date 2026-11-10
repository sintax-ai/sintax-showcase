-- Checks for migration 004. Paste into the Supabase SQL Editor and Run.
--
-- It impersonates portal users and tries allowed and forbidden things, then
-- saves fictional requests to see whether an alert would be queued. Everything
-- is undone inside the script: no email is sent, because the queued request is
-- rolled back before the sender ever sees it.
-- Result: one row per check. Every row should say passed = true.

create temp table if not exists check_results
  (n int, check_name text, expected text, actual text, passed boolean);
truncate check_results;

create or replace function pg_temp.act_as(uid uuid, r text) returns void
language sql as $$
  select set_config('request.jwt.claims',
                    json_build_object('sub', uid, 'role', r)::text, true),
         set_config('role', r, true);
$$;

do $$
declare
  biz      uuid := 'd00c0c1d-7687-5992-bfbd-b69a214c566e';  -- Example Studios
  member   uuid;
  other    uuid;
  got      text;
  rc       int;
  has_net  boolean := exists (select 1 from pg_extension where extname = 'pg_net');
  has_key  boolean := false;
  queued_before int;
  queued_after  int;
  last_body jsonb;
begin
  select user_id into member from public.memberships
  where business_id = biz
  order by (user_id = 'aaaaaaaa-0000-4000-8000-000000000001') desc limit 1;
  if member is null then
    raise exception 'Setup missing: need a Example Studios membership.';
  end if;
  if exists (select 1 from pg_namespace where nspname = 'vault') then
    execute $q$select exists (select 1 from vault.decrypted_secrets
                              where name = 'resend_api_key' and decrypted_secret <> '')$q$ into has_key;
  end if;

  -- 1. Column present
  select count(*)::text into got from information_schema.columns
  where table_schema = 'public' and table_name = 'businesses' and column_name = 'notify_email';
  insert into check_results values (1, 'Alert email column exists on businesses', '1', got, got = '1');

  -- 2. Settings present
  select count(*)::text into got from public.sintax_settings where key in ('alert_from', 'alert_reply_to', 'portal_url');
  insert into check_results values (2, 'Sender, reply-to and portal link settings exist', '3', got, got = '3');

  -- 3. Member cannot read settings
  begin
    perform pg_temp.act_as(member, 'authenticated');
    select count(*)::text into got from public.sintax_settings;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (3, 'Member cannot read SINTAX settings', 'error 42501', got, got = 'error 42501');

  -- 4. Member cannot change where alerts go
  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.businesses set notify_email = 'hijack@example.test' where id = biz;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (4, 'Member cannot change the alert email', 'error 42501', got, got = 'error 42501');

  -- 5. Malformed email refused
  begin
    update public.businesses set notify_email = 'not an email' where id = biz;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (5, 'Malformed alert email refused', 'error 23514', got, got = 'error 23514');

  -- 6. A request for a business with no alert email is saved and queues nothing
  begin
    other := gen_random_uuid();
    insert into public.businesses (id, name, slug)
    values (other, 'FICTIONAL Silent Business', 'fictional-silent-' || left(other::text, 8));
    if has_net then execute 'select count(*) from net.http_request_queue' into queued_before; else queued_before := 0; end if;
    insert into public.inquiries (business_id, source_id, name, phone, service, preferred_time, duration, preferences)
    values (other, 'fictional-quiet', 'FICTIONAL Quiet Caller', '+15555550100', 'recording', 'Friday 3pm', '2 hours', '');
    if has_net then execute 'select count(*) from net.http_request_queue' into queued_after; else queued_after := 0; end if;
    got := format('saved, queued %s', queued_after - queued_before);
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate || ' ' || sqlerrm; end if;
  end;
  insert into check_results values (6, 'No alert email set: request saved, nothing queued', 'saved, queued 0', got, got = 'saved, queued 0');

  -- 7. A request for a business WITH an alert email is saved, and an alert is
  --    queued to Resend when the key and pg_net are present. Rolled back: nothing is sent.
  begin
    other := gen_random_uuid();
    insert into public.businesses (id, name, slug, notify_email)
    values (other, 'FICTIONAL Alerted Business', 'fictional-alert-' || left(other::text, 8), 'fictional-owner@example.test');
    if has_net then execute 'select count(*) from net.http_request_queue' into queued_before; else queued_before := 0; end if;
    insert into public.inquiries (business_id, source_id, name, phone, service, preferred_time, duration, preferences)
    values (other, 'fictional-loud', 'FICTIONAL Loud Caller', '+15555550101', 'recording_light_mix', 'Saturday 2pm', '3 hours', 'Studio A');
    if has_net and has_key then
      execute 'select count(*) from net.http_request_queue' into queued_after;
      -- pg_net stores the body as jsonb in older versions and bytea in newer ones.
      if exists (select 1 from information_schema.columns
                 where table_schema = 'net' and table_name = 'http_request_queue'
                   and column_name = 'body' and data_type = 'bytea') then
        execute 'select convert_from(body, ''utf8'')::jsonb from net.http_request_queue order by id desc limit 1' into last_body;
      else
        execute 'select body::jsonb from net.http_request_queue order by id desc limit 1' into last_body;
      end if;
      got := format('saved, queued %s, to %s, subject "%s"',
                    queued_after - queued_before,
                    last_body -> 'to' ->> 0,
                    last_body ->> 'subject');
    else
      got := 'saved, alert skipped (' || case when not has_net then 'pg_net missing' else 'no key in Vault yet' end || ')';
    end if;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate || ' ' || sqlerrm; end if;
  end;
  insert into check_results values (7, 'Alert email set: request saved and one email queued to the owner',
    case when has_net and has_key
         then 'saved, queued 1, to fictional-owner@example.test, subject "New call: FICTIONAL Loud Caller wants recording light mix, Saturday 2pm"'
         else 'saved, alert skipped (...)' end,
    got,
    case when has_net and has_key
         then got = 'saved, queued 1, to fictional-owner@example.test, subject "New call: FICTIONAL Loud Caller wants recording light mix, Saturday 2pm"'
         else got like 'saved, alert skipped%' end);

  -- 8. Members cannot run the alert function by hand
  begin
    perform pg_temp.act_as(member, 'authenticated');
    execute 'select public.inquiries_alert_owner()';
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (8, 'Member cannot call the alert function', 'error 42501 or 0A000', got,
    got in ('error 42501', 'error 0A000'));
end $$;

select n, check_name, passed, expected, actual from check_results order by n;
