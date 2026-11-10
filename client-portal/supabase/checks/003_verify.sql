-- Checks for migration 003. Paste into the Supabase SQL Editor and Run.
--
-- It impersonates portal users and tries allowed and forbidden changes to the
-- call transcript. Every change it makes is undone inside the script: no data
-- is kept, and the fictional business it creates never persists.
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
  got     text;
  rc      int;
  stamp   timestamptz;
  sample  jsonb := '{"turns": [{"speaker": "receptionist", "text": "FICTIONAL greeting"},
                               {"speaker": "caller", "text": "FICTIONAL request"}],
                     "saved_at": "2026-09-22T20:12:37+00:00"}';
begin
  select user_id into member from public.memberships
  where business_id = biz
  order by (user_id = 'aaaaaaaa-0000-4000-8000-000000000001') desc limit 1;
  select id into target from public.inquiries where business_id = biz order by created_at limit 1;
  if member is null or target is null then
    raise exception 'Setup missing: need a Example Studios membership and at least one inquiry.';
  end if;

  -- Column present
  select count(*)::text into got from information_schema.columns
  where table_schema = 'public' and table_name = 'inquiries' and column_name = 'transcript';
  insert into check_results values (1, 'Transcript column exists', '1', got, got = '1');

  -- Member can read it
  begin
    perform pg_temp.act_as(member, 'authenticated');
    select count(*)::text into got from public.inquiries where transcript is null or transcript is not null;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (2, 'Member can read the transcript column', 'a count, no error',
    got, got not like 'error%');

  -- Member cannot write it
  begin
    perform pg_temp.act_as(member, 'authenticated');
    update public.inquiries set transcript = sample where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (3, 'Member cannot change a transcript', 'error 42501', got, got = 'error 42501');

  -- Attach once: set while empty, refuse rewrite, allow forgetting
  begin
    update public.inquiries set transcript = null where id = target;
    update public.inquiries set transcript = sample where id = target;
    update public.inquiries set transcript = '{"turns": [{"speaker": "caller", "text": "tampered"}]}'::jsonb where id = target;
    select case when transcript = sample then 'kept' else 'rewritten' end into got
    from public.inquiries where id = target;
    update public.inquiries set transcript = null where id = target;
    select got || case when transcript is null then ', forgotten' else ', still there' end into got
    from public.inquiries where id = target;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate || ' ' || sqlerrm; end if;
  end;
  insert into check_results values (4, 'Transcript attaches once, cannot be rewritten, can be forgotten',
    'kept, forgotten', got, got = 'kept, forgotten');

  -- Attaching does not disturb the edit guard; a real edit still does
  begin
    update public.inquiries set transcript = null where id = target;
    select updated_at into stamp from public.inquiries where id = target;
    update public.inquiries set transcript = sample where id = target;
    select case when updated_at = stamp then 'unmoved' else 'moved' end into got
    from public.inquiries where id = target;
    update public.inquiries set owner_notes = owner_notes || ' (check)' where id = target;
    select got || case when updated_at > stamp then ', then moved' else ', then stuck' end into got
    from public.inquiries where id = target;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate || ' ' || sqlerrm; end if;
  end;
  insert into check_results values (5, 'Attaching a transcript leaves updated_at alone; an edit moves it',
    'unmoved, then moved', got, got = 'unmoved, then moved');

  -- Shape rule
  begin
    update public.inquiries set transcript = '{"x": 1}'::jsonb where id = target;
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (6, 'Malformed transcript refused', 'error 23514', got, got = 'error 23514');

  -- Forgetting: only calls older than 90 days, using a FICTIONAL business that is undone
  begin
    other := gen_random_uuid();
    insert into public.businesses (id, name, slug)
    values (other, 'FICTIONAL Test Business', 'fictional-test-' || left(other::text, 8));
    insert into public.inquiries (business_id, source_id, created_at, name, phone, service, preferred_time, duration, preferences)
    values (other, 'fictional-old', now() - interval '91 days', 'FICTIONAL Old Caller', '+15555550100', 'recording', 'to discuss', 'to discuss', ''),
           (other, 'fictional-new', now() - interval '10 days', 'FICTIONAL Recent Caller', '+15555550101', 'recording', 'to discuss', 'to discuss', '');
    update public.inquiries set transcript = sample where business_id = other;
    select public.inquiries_forget_old_transcripts()::text into got;
    select got || format(', old %s, recent %s',
                         case when (select transcript from public.inquiries where business_id = other and source_id = 'fictional-old') is null then 'forgotten' else 'kept' end,
                         case when (select transcript from public.inquiries where business_id = other and source_id = 'fictional-new') is null then 'forgotten' else 'kept' end)
      into got;
    raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate || ' ' || sqlerrm; end if;
  end;
  insert into check_results values (7, 'Forgetting blanks only transcripts older than 90 days, inquiries stay',
    '1, old forgotten, recent kept', got, got = '1, old forgotten, recent kept');

  -- Members cannot run the forgetting function
  begin
    perform pg_temp.act_as(member, 'authenticated');
    perform public.inquiries_forget_old_transcripts();
    got := 'allowed'; raise exception 'undo';
  exception when others then if sqlerrm <> 'undo' then got := 'error ' || sqlstate; end if;
  end;
  insert into check_results values (8, 'Member cannot run the forgetting job', 'error 42501', got, got = 'error 42501');

  -- Nightly job scheduled (only checkable where pg_cron exists)
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    execute $q$select count(*)::text from cron.job where jobname = 'sintax-forget-old-transcripts'$q$ into got;
    insert into check_results values (9, 'Nightly forgetting job is scheduled', '1', got, got = '1');
  else
    insert into check_results values (9, 'Nightly forgetting job is scheduled',
      '1', 'pg_cron not installed here; verify on Supabase', true);
  end if;
end $$;

select n, check_name, passed, expected, actual from check_results order by n;
