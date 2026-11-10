-- Fictional, rollback-contained settings checks. No invitations or real emails.
create temp table if not exists settings_check_results(check_name text, passed boolean);
truncate settings_check_results;
do $$
declare
  member_id uuid;
  own_biz uuid := gen_random_uuid(); member_biz uuid := gen_random_uuid(); other_biz uuid := gen_random_uuid();
  request_id uuid := gen_random_uuid(); stamp timestamptz; n int; denied boolean; checks jsonb := '[]';
begin
  select user_id into member_id from public.memberships limit 1;
  if member_id is null then raise exception 'Needs one existing member'; end if;
  begin
    insert into public.businesses(id,name,slug) values
      (own_biz,'FICTIONAL settings owner','settings-check-'||own_biz),
      (member_biz,'FICTIONAL settings member','settings-check-'||member_biz),
      (other_biz,'FICTIONAL settings other','settings-check-'||other_biz);
    insert into public.memberships(business_id,user_id,role) values
      (own_biz,member_id,'owner'),(member_biz,member_id,'member');
    select settings_updated_at into stamp from public.businesses where id=own_biz;
    perform set_config('request.jwt.claim.sub',member_id::text,true);
    perform set_config('request.jwt.claims',json_build_object('sub',member_id,'role','authenticated')::text,true);
    perform set_config('role','authenticated',true);
    update public.businesses set notify_email='requests@example.test',request_alerts_enabled=false
      where id=own_biz and settings_updated_at=stamp;
    get diagnostics n=row_count;
    if n<>1 then raise exception 'Owner preference update failed'; end if;
    if (select notify_email from public.businesses where id=own_biz)<>'requests@example.test'
      or (select request_alerts_enabled from public.businesses where id=own_biz) then raise exception 'Disabled alerts lost address'; end if;
    checks := checks || '["Owners can update only their alert preferences; disabled retains recipient"]';
    update public.businesses set request_alerts_enabled=true where id=own_biz and settings_updated_at=stamp;
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Stale preference update accepted'; end if;
    checks := checks || '["Preference versions reject stale updates"]';
    update public.businesses set notify_email='wrong@example.test' where id in(member_biz,other_biz);
    get diagnostics n=row_count;
    if n<>0 then raise exception 'Member or cross-business update accepted'; end if;
    checks := checks || '["Members and unrelated owners cannot change another business preferences"]';
    denied := false;
    begin update public.businesses set name='Changed' where id=own_biz;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Business metadata update granted'; end if;
    denied := false;
    begin update public.memberships set role='owner' where business_id=member_biz;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Self promotion allowed'; end if;
    checks := checks || '["Clients cannot rename businesses or promote themselves"]';
    denied := false;
    begin update public.businesses set notify_email='not-an-email' where id=own_biz;
    exception when check_violation then denied := true; end;
    if not denied then raise exception 'Invalid alert address accepted'; end if;
    checks := checks || '["Server validates alert email format"]';
    insert into public.receptionist_change_requests(id,business_id,category,description)
      values(request_id,member_biz,'hours','FICTIONAL: close at five on Fridays.');
    if (select requested_by from public.receptionist_change_requests where id=request_id)<>member_id
      or (select status from public.receptionist_change_requests where id=request_id)<>'submitted' then
      raise exception 'Submission attribution or default status incorrect'; end if;
    checks := checks || '["Members can submit requests attributed to their authenticated identity"]';
    denied := false;
    begin insert into public.receptionist_change_requests(business_id,category,description)
      values(other_biz,'voice','FICTIONAL change');
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Cross-business submission allowed'; end if;
    checks := checks || '["Cross-business change submissions are denied"]';
    denied := false;
    begin insert into public.receptionist_change_requests(business_id,category,description,status)
      values(own_biz,'voice','FICTIONAL change','completed');
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Client supplied status accepted'; end if;
    denied := false;
    begin insert into public.receptionist_change_requests(business_id,category,description,requested_by)
      values(own_biz,'voice','FICTIONAL change',member_id);
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Client supplied requester accepted'; end if;
    denied := false;
    begin insert into public.receptionist_change_requests(business_id,category,description,created_at)
      values(own_biz,'voice','FICTIONAL change',now());
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Client supplied timestamp accepted'; end if;
    checks := checks || '["Clients cannot forge review status, requester or timestamps"]';
    denied := false;
    begin update public.receptionist_change_requests set status='completed' where id=request_id;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Client review update allowed'; end if;
    denied := false;
    begin delete from public.receptionist_change_requests where id=request_id;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Client request deletion allowed'; end if;
    checks := checks || '["Submitted change requests cannot be altered or deleted by clients"]';
    denied := false;
    begin insert into public.receptionist_change_requests(business_id,category,description)
      values(own_biz,'unknown','FICTIONAL change');
    exception when check_violation then denied := true; end;
    if not denied then raise exception 'Unknown request category accepted'; end if;
    denied := false;
    begin insert into public.receptionist_change_requests(business_id,category,description)
      values(own_biz,'hours',E' \t\n');
    exception when check_violation then denied := true; end;
    if not denied then raise exception 'Whitespace request accepted'; end if;
    denied := false;
    begin insert into public.receptionist_change_requests(business_id,category,description)
      values(own_biz,'hours',repeat('x',4001));
    exception when check_violation then denied := true; end;
    if not denied then raise exception 'Oversized request accepted'; end if;
    checks := checks || '["Request category and description boundaries are enforced by the server"]';
    denied := false;
    begin insert into public.receptionist_change_requests(id,business_id,category,description)
      values(request_id,member_biz,'hours','FICTIONAL duplicate');
    exception when unique_violation then denied := true; end;
    if not denied then raise exception 'Duplicate request ID accepted'; end if;
    checks := checks || '["Stable submission IDs prevent duplicate requests"]';
    perform set_config('role','none',true);
    insert into public.receptionist_change_requests(business_id,requested_by,category,description)
      values(other_biz,member_id,'voice','FICTIONAL private request');
    perform set_config('role','authenticated',true);
    select count(*) into n from public.receptionist_change_requests where business_id=other_biz;
    if n<>0 then raise exception 'Other business request visible'; end if;
    checks := checks || '["Change request reads remain isolated by business"]';
    perform set_config('role','anon',true);
    denied := false;
    begin perform id from public.receptionist_change_requests limit 1;
    exception when insufficient_privilege then denied := true; end;
    if not denied then raise exception 'Anonymous requests readable'; end if;
    checks := checks || '["Anonymous users cannot access change requests"]';
    perform set_config('role','none',true);
    if sintax_private.customer_request_alert_recipient(own_biz) is not null then raise exception 'Disabled alert returns recipient'; end if;
    update public.businesses set request_alerts_enabled=true where id=own_biz;
    if sintax_private.customer_request_alert_recipient(own_biz)<>'requests@example.test' then raise exception 'Enabled alert recipient incorrect'; end if;
    if has_function_privilege('authenticated','sintax_private.customer_request_alert_recipient(uuid)','EXECUTE')
       or has_function_privilege('authenticated','sintax_private.receptionist_change_notify_sintax()','EXECUTE') then
      raise exception 'Private alert functions exposed'; end if;
    checks := checks || '["Alert delivery selects the retained address only when enabled; helpers stay private"]';
    raise exception 'rollback settings fixtures';
  exception when raise_exception then
    if sqlerrm<>'rollback settings fixtures' then raise; end if;
  end;
  if exists(select 1 from public.businesses where id in(own_biz,member_biz,other_biz)) then raise exception 'Settings fixtures leaked'; end if;
  insert into settings_check_results select value,true from jsonb_array_elements_text(checks);
end $$;
select * from settings_check_results;
