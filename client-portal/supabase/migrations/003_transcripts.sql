-- 003 - Keep what was said on each call, so an owner can check before correcting.
--
-- Additive only: no rows deleted, no columns dropped, existing values untouched.
-- Runs as one transaction: if any step fails, nothing is changed.
-- Requires 002_owner_corrections.sql (already applied remotely). This file
-- replaces the trigger function from 002 with a wider version; do not re-run
-- 002 after this one.
--
-- What it does:
--   1. `transcript` - the call's spoken turns as JSON, attached by the voice
--                     agent after the caller hangs up. Text only. No audio is
--                     ever recorded.
--   2. Attach once  - a transcript can be set while empty and later removed,
--                     but never rewritten. Enforced by the row trigger, for
--                     every role including administrators.
--   3. Read-only in the portal - members can read it through the existing
--                     select grant. `transcript` is deliberately NOT added to
--                     the update grant, so a signed-in owner cannot change it.
--   4. Forgotten after 90 days - a nightly job blanks transcripts of calls older
--                     than 90 days. The inquiry itself is kept. Uses pg_cron
--                     when available (Supabase has it); otherwise see the notice.
--   5. Attaching a transcript does not count as an edit: `updated_at` is only
--                     bumped when something other than the transcript changes,
--                     so an owner with the request open is not told it changed.
begin;

-- 1. Column and shape rule. Shape: {"turns": [{"speaker": ..., "text": ...}, ...], ...}
alter table public.inquiries add column if not exists transcript jsonb;

do $$
begin
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.inquiries'::regclass
                   and conname = 'inquiries_transcript_valid') then
    alter table public.inquiries add constraint inquiries_transcript_valid check (
      transcript is null
      or (coalesce(jsonb_typeof(transcript -> 'turns'), 'missing') = 'array'
          and length(transcript::text) <= 200000));
  end if;
end $$;

-- 2 and 5. Row trigger: frozen original (from 002), attach-once transcript,
--          and updated_at that ignores transcript-only changes.
create or replace function public.inquiries_protect_capture()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    -- Always the database's own copy; a client-supplied `original` is ignored.
    new.original := jsonb_build_object(
      'name', new.name, 'phone', new.phone, 'service', new.service,
      'preferred_time', new.preferred_time, 'duration', new.duration,
      'preferences', new.preferences);
    new.updated_at := coalesce(new.created_at, now());
  else
    new.original := old.original;  -- frozen, even for administrators
    -- A transcript may be attached once (empty -> value) and forgotten
    -- (value -> empty), never rewritten.
    if old.transcript is not null and new.transcript is not null
       and new.transcript is distinct from old.transcript then
      new.transcript := old.transcript;
    end if;
    -- Only a real edit moves the edit guard. Attaching or forgetting a
    -- transcript leaves an owner's open request undisturbed.
    if (to_jsonb(new) - 'transcript' - 'updated_at')
       is distinct from (to_jsonb(old) - 'transcript' - 'updated_at') then
      new.updated_at := now();
    else
      new.updated_at := old.updated_at;
    end if;
  end if;
  return new;
end $$;

drop trigger if exists inquiries_protect_capture on public.inquiries;
create trigger inquiries_protect_capture
  before insert or update on public.inquiries
  for each row execute function public.inquiries_protect_capture();

-- 4. Forgetting. Returns how many transcripts were blanked.
create or replace function public.inquiries_forget_old_transcripts()
returns integer
language plpgsql
set search_path = ''
as $$
declare
  n integer;
begin
  update public.inquiries
  set transcript = null
  where transcript is not null
    and created_at < now() - interval '90 days';
  get diagnostics n = row_count;
  return n;
end $$;

-- Only the database itself (and the nightly job) may run it; portal users may not.
revoke execute on function public.inquiries_forget_old_transcripts() from public;
revoke execute on function public.inquiries_forget_old_transcripts() from anon, authenticated;

-- Nightly at 04:15 UTC. Re-running this file updates the job in place.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron')
     and not exists (select 1 from pg_extension where extname = 'pg_cron') then
    begin
      create extension if not exists pg_cron;
    exception when others then
      raise notice 'pg_cron could not be enabled from here (%). In Supabase: Database > Extensions > enable pg_cron, then run this file again.', sqlerrm;
    end;
  end if;
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.schedule('sintax-forget-old-transcripts', '15 4 * * *',
                          'select public.inquiries_forget_old_transcripts()');
  else
    raise notice 'pg_cron is not available here, so transcripts older than 90 days are not forgotten automatically. Run: select public.inquiries_forget_old_transcripts();';
  end if;
end $$;

commit;
