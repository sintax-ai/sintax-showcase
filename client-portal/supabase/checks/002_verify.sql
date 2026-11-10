-- Checks for migration 002. Paste into the Supabase SQL Editor and Run.
--
-- It impersonates portal users and tries allowed and forbidden changes.
-- Every change it makes is undone inside the script: no data is kept, and the
-- fictional business it creates for the cross-client test never persists.
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
  biz     uuid := 'd00c0c1d-7687-5992-bfbd-b69a214c566e';  -- Example Studios
  member  uuid;
  target  uuid;
  other   uuid;
  total   int;
  got     text;
  rc      int;
begin
  select user_id into member from public.memberships
  where business_id = biz
  order by (user_id = 'aaaaaaaa-0000-4000-8000-000000000001') desc limit 1;
  select id into target from public.inquiries where business_id = biz order by created_at limit 1;
  select count(*) into total from public.inquiries where business_id = biz;
  if member is null or target is null then
    raise exception 'Setup missing: need a Example Studios membership and at least one inquiry.';
  end if;

  -- Snapshot backfill
  select count(*)::text into got from public.inquiries where original is null;
  insert into check_results values (1, 'Every inquiry has its captured original', '0 missing', got || ' missing', got = '0');

  -- Reads
  begin
    perform pg_temp.act_as(member, 'authenticated');
    select count(*)::text into got from public.inquiries;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (2, 'Member sees all Example Studios inquiries', total::text, got, got = total::text);

  begin
    perform pg_temp.act_as(gen_random_uuid(), 'authenticated');
    select count(*)::text into got from public.inquiries;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (3, 'Signed-in stranger sees nothing', '0', got, got = '0');

  begin
    perform pg_temp.act_as(null, 'anon');
    select count(*)::text into got from public.inquiries;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (4, 'Anonymous visitor refused', 'error 42501', got, got = 'error 42501');

  -- Allowed correction keeps the original
  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set name = 'Corrected Name Test', service = 'mixing',
      preferred_time = 'Sunday 2pm', status = 'contacted' where id = target;
    get diagnostics rc = row_count;
    select format('%s row; name=%s; original name unchanged=%s', rc, name,
                  case when original->>'name' is distinct from 'Corrected Name Test' then 'yes' else 'no' end)
      into got from public.inquiries where id = target;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (5, 'Member corrects captured fields; original kept',
    '1 row; name=Corrected Name Test; original name unchanged=yes', got,
    got = '1 row; name=Corrected Name Test; original name unchanged=yes');

  -- Restricted columns
  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set business_id = gen_random_uuid() where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (6, 'Member cannot move an inquiry to another business', 'error 42501', got, got = 'error 42501');

  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set original = '{}'::jsonb where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (7, 'Member cannot rewrite the captured original', 'error 42501', got, got = 'error 42501');

  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set source_id = 'x', created_at = now() where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (8, 'Member cannot change source ID or received time', 'error 42501', got, got = 'error 42501');

  begin
    perform pg_temp.act_as(member, 'authenticated');
    delete from public.inquiries where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (9, 'Member cannot delete an inquiry', 'error 42501', got, got = 'error 42501');

  begin
    perform pg_temp.act_as(member, 'authenticated');
    insert into public.memberships (business_id, user_id, role) values (gen_random_uuid(), member, 'member');
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (10, 'Member cannot grant themselves a membership', 'error 42501', got, got = 'error 42501');

  -- Format rules
  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set phone = '555-1234' where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (11, 'Invalid phone refused', 'error 23514', got, got = 'error 23514');

  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set name = '   ' where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (12, 'Blank name refused', 'error 23514', got, got = 'error 23514');

  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set service = 'Recording Session' where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (13, 'Free-typed service refused', 'error 23514', got, got = 'error 23514');

  -- Cross-business isolation, using a FICTIONAL business that is undone afterwards
  begin
    other := gen_random_uuid();
    insert into public.businesses (id, name, slug) values (other, 'FICTIONAL Test Business', 'fictional-test-' || left(other::text, 8));
    insert into public.inquiries (business_id, source_id, name, phone, service, preferred_time, duration, preferences, status, owner_notes)
    values (other, 'fictional-1', 'FICTIONAL Caller', '+15555550100', 'recording', 'to discuss', 'to discuss', '', 'new', '');
    perform pg_temp.act_as(member, 'authenticated');
    select count(*) into rc from public.inquiries where business_id = other;
    got := format('sees %s', rc);
    update public.inquiries set name = 'Hijacked', status = 'closed' where business_id = other;
    get diagnostics rc = row_count;
    got := got || format(', updates %s', rc);
    select count(*) into rc from public.businesses where id = other;
    got := got || format(', sees business %s', rc);
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate || ' ' || sqlerrm; end if;
  end;
  insert into check_results values (14, 'Example Studios member cannot read or edit another business',
    'sees 0, updates 0, sees business 0', got, got = 'sees 0, updates 0, sees business 0');

  -- Upload retry must not overwrite an owner correction
  begin
    update public.inquiries set name = 'Owner Correction' where id = target;
    insert into public.inquiries (business_id, source_id, name, phone, service, preferred_time, duration, preferences)
    select business_id, source_id, original->>'name', original->>'phone', original->>'service',
           original->>'preferred_time', original->>'duration', original->>'preferences'
    from public.inquiries where id = target
    on conflict (business_id, source_id) do nothing;
    select format('name=%s; copies=%s', i.name,
                  (select count(*) from public.inquiries j where j.business_id = i.business_id and j.source_id = i.source_id))
      into got from public.inquiries i where id = target;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (15, 'Upload retry keeps the owner''s correction, no duplicate',
    'name=Owner Correction; copies=1', got, got = 'name=Owner Correction; copies=1');

  -- Even an administrator cannot alter the captured original
  begin
    update public.inquiries set original = '{"name":"tampered"}'::jsonb where id = target;
    select case when original->>'name' = 'tampered' then 'changed' else 'unchanged' end into got
    from public.inquiries where id = target;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (16, 'Captured original is frozen, even for administrators', 'unchanged', got, got = 'unchanged');
end $$;

select n, check_name, passed, expected, actual from check_results order by n;
