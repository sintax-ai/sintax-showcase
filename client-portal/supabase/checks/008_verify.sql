-- Rollback-contained recipient checks. Never inserts inquiries or sends emails.
create temp table if not exists alert_copy_check_results(check_name text, passed boolean);
truncate alert_copy_check_results;
do $$
declare
  biz uuid := gen_random_uuid(); other_biz uuid := gen_random_uuid();
  checks jsonb := '[]';
begin
  if to_regprocedure('sintax_private.customer_request_alert_addresses(uuid)') is null then
    raise exception 'Multiple alert recipient support is missing';
  end if;
  begin
    insert into public.businesses(id,name,slug,notify_email) values
      (biz,'FICTIONAL copy check','copy-check-'||biz,'owner@example.test'),
      (other_biz,'FICTIONAL other','copy-check-'||other_biz,'other@example.test');
    if sintax_private.customer_request_alert_addresses(biz) <> '{"to":["owner@example.test"]}'::jsonb then
      raise exception 'Single-recipient behavior changed'; end if;
    checks := checks || '["Existing businesses retain single-recipient delivery"]';
    insert into public.sintax_settings(key,value) values('alert_cc:'||biz,'support@example.test');
    if sintax_private.customer_request_alert_addresses(biz) <> '{"to":["owner@example.test"],"cc":["support@example.test"]}'::jsonb then
      raise exception 'Copy recipient missing'; end if;
    checks := checks || '["Approved copy recipient joins the primary recipient"]';
    if sintax_private.customer_request_alert_addresses(other_biz) <> '{"to":["other@example.test"]}'::jsonb then
      raise exception 'Copy recipient leaked across businesses'; end if;
    checks := checks || '["Copy configuration is isolated by business"]';
    update public.businesses set request_alerts_enabled=false where id=biz;
    if sintax_private.customer_request_alert_addresses(biz) <> '{}'::jsonb then
      raise exception 'Disabled alerts retain recipients'; end if;
    checks := checks || '["Disabling alerts suppresses primary and copy together"]';
    update public.businesses set request_alerts_enabled=true where id=biz;
    update public.sintax_settings set value='OWNER@example.test' where key='alert_cc:'||biz;
    if sintax_private.customer_request_alert_addresses(biz) <> '{"to":["owner@example.test"]}'::jsonb then
      raise exception 'Duplicate address not removed'; end if;
    checks := checks || '["Duplicate primary and copy addresses deliver once"]';
    update public.sintax_settings set value='bad,other@example.test' where key='alert_cc:'||biz;
    if sintax_private.customer_request_alert_addresses(biz) <> '{"to":["owner@example.test"]}'::jsonb then
      raise exception 'Malformed copy address accepted'; end if;
    checks := checks || '["Invalid copy settings do not break primary delivery"]';
    if has_function_privilege('authenticated','sintax_private.customer_request_alert_addresses(uuid)','EXECUTE')
      or has_function_privilege('anon','sintax_private.customer_request_alert_addresses(uuid)','EXECUTE')
      or has_table_privilege('authenticated','public.sintax_settings','UPDATE') then
      raise exception 'Private configuration exposed'; end if;
    checks := checks || '["Clients cannot read the private helper or edit copy configuration"]';
    raise exception 'rollback copy fixtures';
  exception when raise_exception then
    if sqlerrm <> 'rollback copy fixtures' then raise; end if;
  end;
  if exists(select 1 from public.businesses where id in(biz,other_biz)) then
    raise exception 'Copy check fixtures leaked'; end if;
  insert into alert_copy_check_results select value,true from jsonb_array_elements_text(checks);
end $$;
select * from alert_copy_check_results;
