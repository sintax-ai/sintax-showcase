-- Fictional fixtures only. The inner transaction is rolled back, including all
-- role switches, memberships and inquiries. No alerts: fixture businesses have
-- no notify_email. No existing customer records are changed.
create temp table if not exists trash_check_results (check_name text, passed boolean);
truncate trash_check_results;
do $$
declare
  member_id uuid;
  own_biz uuid := gen_random_uuid();
  other_biz uuid := gen_random_uuid();
  target uuid := gen_random_uuid();
  other_target uuid := gen_random_uuid();
  stamp timestamptz;
  captured jsonb;
  got jsonb;
  n int;
  denied boolean;
  checks jsonb := '[]'::jsonb;
begin
  select user_id into member_id from public.memberships limit 1;
  if member_id is null then raise exception 'Verification needs one existing member account'; end if;
  begin
    insert into public.businesses(id,name,slug) values
      (own_biz,'FICTIONAL Trash check','trash-check-' || own_biz),
      (other_biz,'FICTIONAL Other check','trash-check-' || other_biz);
    insert into public.memberships(business_id,user_id) values (own_biz,member_id);
    insert into public.inquiries(id,business_id,source_id,created_at,name,phone,service,preferred_time,duration,status,owner_notes,transcript) values
      (target,own_biz,'trash-check',now()-interval '1 day','FICTIONAL caller','+15555550101','recording','To discuss','1 hour','contacted','Keep this note','{"turns":[{"speaker":"caller","text":"FICTIONAL request"}]}'),
      (other_target,other_biz,'trash-check',now()-interval '1 day','FICTIONAL other','+15555550102','recording','To discuss','1 hour','new','','{"turns":[]}');
    select updated_at, to_jsonb(i)-'is_trashed'-'updated_at' into stamp,captured from public.inquiries i where id=target;

    perform set_config('request.jwt.claims',json_build_object('sub',member_id,'role','authenticated')::text,true);
    perform set_config('request.jwt.claim.sub',member_id::text,true);
    perform set_config('role','authenticated',true);
    update public.inquiries set is_trashed=true where id=target and updated_at=stamp;
    get diagnostics n=row_count;
    if n<>1 then raise exception 'Own-business trash failed'; end if;
    select to_jsonb(i) into got from public.inquiries i where id=target;
    if not (got->>'is_trashed')::boolean or got-'is_trashed'-'updated_at' <> captured then
      raise exception 'Trash lost request data';
    end if;
    checks := checks || '["Member can trash and read own inquiry; all captured data, status, notes and transcript preserved"]';

    update public.inquiries set owner_notes='stale edit' where id=target and updated_at=stamp;
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Trash did not invalidate stale edits'; end if;
    update public.inquiries set is_trashed=false where id=target and updated_at=stamp;
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Stale restore was accepted'; end if;
    checks := checks || '["Stale edits and restores rejected"]';

    select updated_at into stamp from public.inquiries where id=target;
    update public.inquiries set is_trashed=false where id=target and updated_at=stamp;
    get diagnostics n=row_count;
    select to_jsonb(i) into got from public.inquiries i where id=target;
    if n<>1 or (got->>'is_trashed')::boolean or got-'is_trashed'-'updated_at' <> captured then
      raise exception 'Restore failed or lost data';
    end if;
    checks := checks || '["Restore preserves prior follow-up status and data"]';

    select count(*) into n from public.inquiries where id=other_target;
    if n<>0 then raise exception 'Other business visible'; end if;
    update public.inquiries set is_trashed=true where id=other_target;
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Other business trash allowed'; end if;
    update public.inquiries set is_trashed=false where id=other_target;
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Other business restore allowed'; end if;
    checks := checks || '["Other business cannot be read, trashed or restored"]';

    denied := false;
    begin
      delete from public.inquiries where id=target;
    exception when insufficient_privilege then denied := true;
    end;
    if not denied then raise exception 'Permanent delete privilege was granted'; end if;
    checks := checks || '["Permanent deletion remains forbidden"]';
    denied := false;
    begin
      update public.inquiries set business_id=other_biz where id=target;
    exception when insufficient_privilege then denied := true;
    end;
    if not denied then raise exception 'Business reassignment allowed'; end if;
    checks := checks || '["Business reassignment remains forbidden"]';

    perform set_config('role','none',true);
    perform set_config('request.jwt.claims','{"role":"anon"}',true);
    perform set_config('request.jwt.claim.sub','',true);
    perform set_config('role','anon',true);
    denied := false;
    begin
      update public.inquiries set is_trashed=true where id=target;
    exception when insufficient_privilege then denied := true;
    end;
    if not denied then raise exception 'Anonymous trash privilege was granted'; end if;
    checks := checks || '["Anonymous trash forbidden"]';

    perform set_config('role','none',true);
    -- Simulate a duplicate agent upload after trashing: it must not resurrect it.
    update public.inquiries set is_trashed=true where id=target;
    insert into public.inquiries(business_id,source_id,name,phone,service,preferred_time,duration)
    values(own_biz,'trash-check','FICTIONAL duplicate','+15555550101','recording','To discuss','1 hour')
    on conflict(business_id,source_id) do nothing;
    if not (select is_trashed from public.inquiries where id=target) then raise exception 'Duplicate upload restored inquiry'; end if;
    checks := checks || '["Duplicate upload cannot resurrect a trashed inquiry"]';
    raise exception 'rollback trash fixtures';
  exception when raise_exception then
    if sqlerrm <> 'rollback trash fixtures' then raise; end if;
  end;
  if exists(select 1 from public.businesses where id in (own_biz,other_biz)) then raise exception 'Fixtures not rolled back'; end if;
  insert into trash_check_results select value,true from jsonb_array_elements_text(checks);
end $$;
select * from trash_check_results;
