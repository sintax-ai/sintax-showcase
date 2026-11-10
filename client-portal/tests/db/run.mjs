// Runs the migrations and the Supabase check script against a throwaway
// in-memory PostgreSQL (PGlite). FICTIONAL data only; never touches Supabase.
//   cd tests && npm install && npm run db
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const root = new URL('../../', import.meta.url);
const sql = p => readFileSync(new URL(p, root), 'utf8');
const fresh = async () => { const db = new PGlite(); await db.exec(sql('tests/db/baseline.sql')); await db.exec(sql('supabase/migrations/001_access.sql')); return db; };

// 1. Migration applies, is safe to re-run, and every check passes.
let db = await fresh();
await db.exec(sql('supabase/migrations/002_owner_corrections.sql'));
await db.exec(sql('supabase/migrations/002_owner_corrections.sql'));
await db.exec(sql('supabase/checks/002_verify.sql'));
const { rows } = await db.query('select n, check_name, passed, actual from check_results order by n');
for (const r of rows) console.log(`${r.passed ? 'PASS' : 'FAIL'}  ${String(r.n).padStart(2)}. ${r.check_name}${r.passed ? '' : `  (got: ${r.actual})`}`);
assert.equal(rows.length, 16); assert.ok(rows.every(r => r.passed), 'a database check failed');

// 2. Existing values are untouched by the migration.
const before = await fresh();
const snap = async d => (await d.query('select id, name, phone, service, preferred_time, duration, preferences, status, owner_notes from public.inquiries order by id')).rows;
const original = await snap(before);
await before.exec(sql('supabase/migrations/002_owner_corrections.sql'));
assert.deepEqual(await snap(before), original); console.log('PASS      Existing inquiry values unchanged by migration');

// 3. A row that breaks a format rule stops the migration and changes nothing.
const bad = await fresh();
await bad.exec(`update public.inquiries set phone = '202-555' where source_id = 'fict-1'`);
await assert.rejects(bad.exec(sql('supabase/migrations/002_owner_corrections.sql')));
await bad.exec('rollback');
const cols = await bad.query(`select column_name from information_schema.columns where table_name = 'inquiries' and column_name in ('original','updated_at')`);
assert.equal(cols.rows.length, 0); console.log('PASS      Failed migration leaves the database exactly as it was');

// 4. Migration 003 (call transcripts) applies on top of 002, is safe to re-run, and every check passes.
db = await fresh();
await db.exec(sql('supabase/migrations/002_owner_corrections.sql'));
const snap3 = await snap(db);
await db.exec(sql('supabase/migrations/003_transcripts.sql'));
await db.exec(sql('supabase/migrations/003_transcripts.sql'));
assert.deepEqual(await snap(db), snap3); console.log('PASS      Existing inquiry values unchanged by migration 003');
await db.exec(sql('supabase/checks/003_verify.sql'));
const third = (await db.query('select n, check_name, passed, actual from check_results order by n')).rows;
for (const r of third) console.log(`${r.passed ? 'PASS' : 'FAIL'}  ${String(r.n).padStart(2)}. ${r.check_name}${r.passed ? '' : `  (got: ${r.actual})`}`);
assert.equal(third.length, 9); assert.ok(third.every(r => r.passed), 'a database check failed');

// 5. The 002 checks still hold after 003 (the trigger it replaces must keep freezing `original`).
await db.exec(sql('supabase/checks/002_verify.sql'));
const again = (await db.query('select n, passed, actual from check_results order by n')).rows;
assert.equal(again.length, 16); assert.ok(again.every(r => r.passed), `a 002 check failed after 003: ${JSON.stringify(again.filter(r => !r.passed))}`);
console.log('PASS      All 16 checks from 002 still pass after 003');

// 6. Migration 004 (owner alerts) applies on top of 003, is safe to re-run, and every check passes.
//    The practice database has no pg_net or Vault, so alerts are skipped, never sent.
const snap4 = await snap(db);
await db.exec(sql('supabase/migrations/004_owner_alerts.sql'));
await db.exec(sql('supabase/migrations/004_owner_alerts.sql'));
assert.deepEqual(await snap(db), snap4); console.log('PASS      Existing inquiry values unchanged by migration 004');
await db.exec(sql('supabase/checks/004_verify.sql'));
const fourth = (await db.query('select n, check_name, passed, actual from check_results order by n')).rows;
for (const r of fourth) console.log(`${r.passed ? 'PASS' : 'FAIL'}  ${String(r.n).padStart(2)}. ${r.check_name}${r.passed ? '' : `  (got: ${r.actual})`}`);
assert.equal(fourth.length, 8); assert.ok(fourth.every(r => r.passed), 'a database check failed');

// 7. Earlier checks still hold with the alert trigger in place.
await db.exec(sql('supabase/checks/003_verify.sql'));
assert.ok((await db.query('select passed from check_results')).rows.every(r => r.passed), 'a 003 check failed after 004');
await db.exec(sql('supabase/checks/002_verify.sql'));
assert.ok((await db.query('select passed from check_results')).rows.every(r => r.passed), 'a 002 check failed after 004');
console.log('PASS      All 002 and 003 checks still pass after 004');
console.log('Database checks passed');

// 8. Recoverable Trash preserves existing data, is idempotent, and obeys RLS.
const migration5 = new URL('supabase/migrations/005_trash.sql', root);
const before5 = (await db.query('select to_jsonb(i) as row from public.inquiries i order by id')).rows;
await db.exec(readFileSync(migration5, 'utf8'));
await db.exec(readFileSync(migration5, 'utf8'));
const after5 = (await db.query("select to_jsonb(i)-'is_trashed' as row from public.inquiries i order by id")).rows;
assert.deepEqual(after5, before5);
assert.equal((await db.query('select count(*)::int as n from public.inquiries where is_trashed')).rows[0].n, 0);
await db.exec(sql('supabase/checks/005_verify.sql'));
const fifth = (await db.query('select * from trash_check_results')).rows;
assert.equal(fifth.length, 8);
assert.ok(fifth.every(r => r.passed));
for (const r of fifth) console.log(`PASS      ${r.check_name}`);
for (const check of ['002', '003', '004']) {
  await db.exec(sql(`supabase/checks/${check}_verify.sql`));
  assert.ok((await db.query('select passed from check_results')).rows.every(r => r.passed));
}
console.log('PASS      Earlier database checks still pass after trash migration');

// 9. Separate call history, connected requests, and private recording access.
await db.exec(`create role service_role bypassrls;
create schema storage;
create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);
create table storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text,name text);
alter table storage.objects enable row level security;
grant usage on schema storage to authenticated,anon;
grant select,insert,update,delete on storage.objects to authenticated,anon;`);
const before6 = (await db.query('select to_jsonb(i) as row from public.inquiries i order by id')).rows;
await db.exec(sql('supabase/migrations/006_calls.sql'));
await db.exec(sql('supabase/migrations/006_calls.sql'));
assert.deepEqual((await db.query("select to_jsonb(i)-'call_id' as row from public.inquiries i order by id")).rows, before6);
await db.exec(sql('supabase/checks/006_verify.sql'));
const sixth = (await db.query('select * from call_check_results')).rows;
assert.equal(sixth.length,7);
assert.ok(sixth.every(r=>r.passed));
for (const r of sixth) console.log(`PASS      ${r.check_name}`);
assert.equal((await db.query("select public from storage.buckets where id='call-recordings'")).rows[0].public,false);
console.log('PASS      Private recordings bucket and unchanged existing requests');

// 10. Owner preferences and receptionist-change requests preserve existing data.
const before7 = (await db.query('select to_jsonb(b) as row from public.businesses b order by id')).rows;
const preserved7 = {};
for (const table of ['inquiries','calls','memberships']) {
  preserved7[table] = (await db.query(`select to_jsonb(t) as row from public.${table} t order by to_jsonb(t)::text`)).rows;
}
await db.exec(sql('supabase/migrations/007_account_settings.sql'));
await db.exec(sql('supabase/migrations/007_account_settings.sql'));
assert.deepEqual((await db.query("select to_jsonb(b)-'request_alerts_enabled'-'settings_updated_at' as row from public.businesses b order by id")).rows, before7);
for (const table of Object.keys(preserved7)) {
  assert.deepEqual((await db.query(`select to_jsonb(t) as row from public.${table} t order by to_jsonb(t)::text`)).rows, preserved7[table]);
}
await db.exec(sql('supabase/checks/007_verify.sql'));
const seventh = (await db.query('select * from settings_check_results')).rows;
assert.equal(seventh.length,14);
assert.ok(seventh.every(r=>r.passed));
for (const r of seventh) console.log(`PASS      ${r.check_name}`);

// 11. Optional administrative alert copies remain tenant-scoped and honor opt-out.
await db.exec(sql('supabase/migrations/008_alert_copies.sql'));
await db.exec(sql('supabase/migrations/008_alert_copies.sql'));
await db.exec(sql('supabase/checks/008_verify.sql'));
const eighth = (await db.query('select * from alert_copy_check_results')).rows;
assert.equal(eighth.length,7);
assert.ok(eighth.every(r=>r.passed));
for (const r of eighth) console.log(`PASS      ${r.check_name}`);
