-- Recoverable Trash, shared by every business. No inquiry is deleted.
-- Apply after 004; safe to re-run. Existing membership policies and capture
-- protection stay in force. The existing trigger advances updated_at when this
-- flag changes. Transcript expiry continues independently of trash state.
begin;
alter table public.inquiries add column if not exists is_trashed boolean not null default false;
grant update (is_trashed) on public.inquiries to authenticated;
notify pgrst, 'reload schema';
commit;
