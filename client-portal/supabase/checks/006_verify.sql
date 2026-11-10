-- Fictional fixtures, rolled back inside the exception block. Never changes
-- existing calls or requests. Also runs against the local Storage schema stub.
create temp table if not exists call_check_results(check_name text, passed boolean);
truncate call_check_results;
do $$
declare
  member_id uuid;
  own_biz uuid := gen_random_uuid(); other_biz uuid := gen_random_uuid();
  own_call uuid := gen_random_uuid(); other_call uuid := gen_random_uuid();
  expired_call uuid := gen_random_uuid(); request_id uuid := gen_random_uuid();
  stamp timestamptz; n int; denied boolean; checks jsonb := '[]';
begin
  select user_id into member_id from public.memberships limit 1;
  if member_id is null then raise exception 'Needs one member'; end if;
  begin
    insert into public.businesses(id,name,slug) values
      (own_biz,'FICTIONAL Call check','call-check-'||own_biz),
      (other_biz,'FICTIONAL Other call','call-check-'||other_biz);
    insert into public.memberships(business_id,user_id) values(own_biz,member_id);
    insert into public.calls(id,business_id,session_id,mode,started_at,recording_status,recording_path,recording_expires_at) values
      (own_call,own_biz,'fixture-own','pilot',now()-interval '1 day','available',own_biz||'/'||own_call||'/audio.ogg',null),
      (other_call,other_biz,'fixture-other','demo',now(),'available',other_biz||'/'||other_call||'/audio.ogg',now()+interval '1 day'),
      (expired_call,own_biz,'fixture-expired','pilot',now()-interval '40 days','available',own_biz||'/'||expired_call||'/audio.ogg',now()-interval '1 day');
    insert into storage.objects(bucket_id,name) values
      ('call-recordings',own_biz||'/'||own_call||'/audio.ogg'),
      ('call-recordings',other_biz||'/'||other_call||'/audio.ogg'),
      ('call-recordings',own_biz||'/'||expired_call||'/audio.ogg');
    insert into public.inquiries(id,business_id,source_id,name,phone,service,preferred_time,duration,call_id)
      values(request_id,own_biz,'call-check','FICTIONAL caller','+15555550101','recording','To discuss','To discuss',own_call);
    denied := false;
    begin update public.inquiries set call_id=other_call where id=request_id;
    exception when foreign_key_violation then denied := true; end;
    if not denied then raise exception 'Cross-business request link accepted'; end if;
    checks := checks || '["Request links cannot cross businesses"]';
    select updated_at into stamp from public.calls where id=own_call;
    perform set_config('request.jwt.claim.sub',member_id::text,true);
    perform set_config('request.jwt.claims',json_build_object('sub',member_id,'role','authenticated')::text,true);
    perform set_config('role','authenticated',true);
    select count(*) into n from public.calls where id in(own_call,other_call,expired_call);
    if n<>2 then raise exception 'Call membership isolation failed'; end if;
    checks := checks || '["Members see only their business calls, including calls without requests"]';
    update public.calls set is_trashed=true where id=own_call and updated_at=stamp;
    get diagnostics n=row_count;
    if n<>1 then raise exception 'Call trash failed'; end if;
    update public.calls set is_trashed=false where id=own_call and updated_at=stamp;
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Stale call update allowed'; end if;
    if (select is_trashed from public.inquiries where id=request_id) then raise exception 'Call trash changed request'; end if;
    if (select call_id from public.inquiries where id=request_id)<>own_call then raise exception 'Call trash unlinked request'; end if;
    checks := checks || '["Trash preserves links and requests; stale updates are refused"]';
    select count(*) into n from storage.objects where bucket_id='call-recordings' and name in
      (own_biz||'/'||own_call||'/audio.ogg',other_biz||'/'||other_call||'/audio.ogg',own_biz||'/'||expired_call||'/audio.ogg');
    if n<>1 then raise exception 'Recording isolation or expiry failed'; end if;
    checks := checks || '["Only own unexpired audio is readable, including recoverable trash"]';
    update public.calls set is_trashed=false where id=own_call;
    if (select is_trashed from public.calls where id=own_call) then raise exception 'Restore failed'; end if;
    update public.calls set is_trashed=true where id=other_call;
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Other-business trash allowed'; end if;
    checks := checks || '["Restore works and other-business mutations are blocked"]';
    denied := false;
    begin update public.calls set recording_path=null where id=own_call;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Recording metadata editable'; end if;
    denied := false;
    begin update public.inquiries set call_id=null where id=request_id;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Request link editable'; end if;
    denied := false;
    begin delete from public.calls where id=own_call;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Permanent delete allowed'; end if;
    checks := checks || '["Members cannot change recording metadata, links or permanently delete calls"]';
    perform set_config('role','anon',true);
    denied := false;
    begin perform id from public.calls limit 1;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Anonymous call access allowed'; end if;
    checks := checks || '["Anonymous call access denied"]';
    perform set_config('role','none',true);
    raise exception 'rollback call fixtures';
  exception when raise_exception then
    if sqlerrm <> 'rollback call fixtures' then raise; end if;
  end;
  if exists(select 1 from public.businesses where id in(own_biz,other_biz)) then raise exception 'Fixtures leaked'; end if;
  insert into call_check_results select value,true from jsonb_array_elements_text(checks);
end $$;
select * from call_check_results;
