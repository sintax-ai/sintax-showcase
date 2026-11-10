-- Apply after the existing businesses/memberships/inquiries migration.
-- No user is assigned a membership by this migration.
begin;
alter table public.businesses enable row level security;
alter table public.memberships enable row level security;
alter table public.inquiries enable row level security;
revoke all on public.businesses, public.memberships, public.inquiries from anon, authenticated;
grant select on public.businesses, public.memberships, public.inquiries to authenticated;
grant update (status, owner_notes) on public.inquiries to authenticated;
create policy memberships_read_self on public.memberships for select to authenticated
using (user_id = (select auth.uid()));
create policy businesses_read_member on public.businesses for select to authenticated
using (id in (select business_id from public.memberships where user_id = (select auth.uid())));
create policy inquiries_read_member on public.inquiries for select to authenticated
using (business_id in (select business_id from public.memberships where user_id = (select auth.uid())));
create policy inquiries_update_member on public.inquiries for update to authenticated
using (business_id in (select business_id from public.memberships where user_id = (select auth.uid())))
with check (business_id in (select business_id from public.memberships where user_id = (select auth.uid())));
commit;
