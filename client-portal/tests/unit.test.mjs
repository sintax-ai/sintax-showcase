// Unit checks for the browser modules. Run: cd tests && npm test
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { cell, csv } from '../public/csv.mjs';
import { validateInquiry, normalizePhone, formatPhone, changedFields, corrections } from '../public/validate.mjs';
import { serviceMenu, serviceLabel } from '../public/services.mjs';
import { dayLabel, groupByDay, monthSummary, summaryText, defaultFilter, transcriptTurns } from '../public/inbox.mjs';
import { STATUSES } from '../public/validate.mjs';

let passed = 0;
const test = (name, fn) => { fn(); passed++; console.log('PASS ', name); };

// All records below are FICTIONAL.
const row = {
  id: 'r1', created_at: '2026-09-17T05:03:01Z', name: 'Alex Example', phone: '+15555550101',
  service: 'recording', preferred_time: 'Sunday 2pm', duration: '2 hours', preferences: 'Studio A',
  status: 'new', owner_notes: '',
  original: { name: 'Alex Sample', phone: '+15555550101', service: 'recording', preferred_time: 'Saturday 2pm', duration: '2 hours', preferences: 'Studio A' },
};
const codes = serviceMenu('sample-studio').map(([c]) => c);

test('CSV: formula-looking cells are neutralised', () => {
  assert.equal(cell('=HYPERLINK("bad")'), '"\'=HYPERLINK(""bad"")"');
  assert.equal(cell('  +123'), '"\'  +123"');
  assert.equal(cell('@SUM(A1)'), '"\'@SUM(A1)"');
  assert.equal(cell('-2+3'), '"\'-2+3"');
  assert.equal(cell('\tcmd'), '"\'\tcmd"');
});
test('CSV: quotes and commas are escaped', () => {
  assert.equal(cell('hello, "Jordan"'), '"hello, ""Jordan"""');
});
test('CSV: header only when empty', () => {
  assert.equal(csv([]).split('\r\n').length, 1);
});
test('CSV: a malicious caller name cannot become a formula', () => {
  const out = csv([{ ...row, name: '=cmd|"/c calc"!A1' }]);
  assert.ok(out.includes('"\'=cmd|""/c calc""!A1"'));
});
test('CSV: phone exported readable, not as a +formula', () => {
  assert.ok(csv([row]).includes('"(555) 555-0101"'));
});
test('CSV: corrections flagged with what the AI originally heard', () => {
  const line = csv([row], { formatDate: () => 'D' }).split('\r\n')[1];
  assert.ok(line.includes('"Yes"'));
  assert.ok(line.includes('"name: Alex Sample; time: Saturday 2pm"'));
});

test('Phone: accepts common US formats', () => {
  for (const p of ['(202) 555-0142', '202-555-0142', '2025550142', '+1 202 555 0142', '1.202.555.0142'])
    assert.equal(normalizePhone(p), '+12025550142', p);
});
test('Phone: rejects short, foreign and lettered numbers', () => {
  for (const p of ['555-0142', '+44 20 7946 0958', '202-555-014x', '', '   ']) assert.equal(normalizePhone(p), null, p);
});
test('Phone: displayed as (202) 555-0142', () => {
  assert.equal(formatPhone('+12025550142'), '(202) 555-0142');
});

test('Validation: a correct correction passes and is cleaned', () => {
  const { clean, errors } = validateInquiry({ ...row, name: '  Alex Example ', phone: '555 555 0101' }, codes);
  assert.equal(errors, null);
  assert.equal(clean.name, 'Alex Example');
  assert.equal(clean.phone, '+15555550101');
});
test('Validation: blank name, bad phone, typed service, blank time all caught', () => {
  const { errors } = validateInquiry({ ...row, name: ' ', phone: '12', service: 'Recording Session', preferred_time: '' }, codes);
  assert.deepEqual(Object.keys(errors).sort(), ['name', 'phone', 'preferred_time', 'service']);
});
test('Validation: over-long fields caught', () => {
  const { errors } = validateInquiry({ ...row, name: 'x'.repeat(201), preferences: 'x'.repeat(2001), owner_notes: 'x'.repeat(10001) }, codes);
  assert.deepEqual(Object.keys(errors).sort(), ['name', 'owner_notes', 'preferences']);
});
test('Validation: unknown status refused', () => {
  assert.ok(validateInquiry({ ...row, status: 'booked' }, codes).errors.status);
});

test('Changes: only edited fields are sent', () => {
  const { clean } = validateInquiry({ ...row, status: 'contacted' }, codes);
  assert.deepEqual(changedFields(clean, row), { status: 'contacted' });
});
test('Corrections: detects which captured fields differ from the original', () => {
  assert.deepEqual(corrections(row), ['name', 'preferred_time']);
  assert.deepEqual(corrections({ ...row, original: null }), []);
});

test('Services: Example Studios menu matches the voice agent', () => {
  assert.deepEqual([...codes].sort(), ['mastering', 'mixing', 'music_rental', 'photo_rental', 'photoshoot', 'recording', 'recording_light_mix']);
});
test('Services: SAMPLECLUB inquiry categories can be corrected in its own portal menu', () => {
  const menu = serviceMenu('sampleclub');
  const allowed = menu.map(([code]) => code);
  assert.ok(allowed.includes('private_event'));
  assert.ok(!allowed.includes('recording'));
  const inquiry = { ...row, service: 'private_event', preferences: 'FICTIONAL event for 12 people' };
  assert.equal(validateInquiry(inquiry, allowed).errors, null);
  const config = JSON.parse(readFileSync(new URL('../../studio-agent/clients/sampleclub/client.json', import.meta.url), 'utf8'));
  assert.deepEqual([...allowed].sort(), [...config.services].sort());
});
test('Services: unfamiliar saved values stay visible, never silently replaced', () => {
  const menu = serviceMenu('another-business', ['vocal_coaching']);
  assert.deepEqual(menu, [['vocal_coaching', 'Vocal coaching']]);
  assert.equal(serviceLabel('recording', serviceMenu('sample-studio')), 'Recording session (with engineer)');
});


// ---------- Inbox organisation ----------
const NOW = new Date(2026, 8, 22, 15, 0);   // Tue Sep 22, 2026, 3 PM local
const at = (y, m, d, h = 12) => new Date(y, m, d, h).toISOString();
const inbox = [
  { id: 'a', created_at: at(2026, 8, 22, 14), status: 'new' },
  { id: 'b', created_at: at(2026, 8, 22, 9), status: 'new' },
  { id: 'c', created_at: at(2026, 8, 21), status: 'contacted' },
  { id: 'd', created_at: at(2026, 8, 19), status: 'new' },
  { id: 'e', created_at: at(2026, 8, 12), status: 'closed' },
  { id: 'f', created_at: at(2025, 11, 30), status: 'closed' },
];

test('Inbox: day labels read like a person would say them', () => {
  assert.equal(dayLabel(inbox[0].created_at, NOW), 'Today');
  assert.equal(dayLabel(inbox[2].created_at, NOW), 'Yesterday');
  assert.match(dayLabel(inbox[3].created_at, NOW), /Saturday/);
  assert.match(dayLabel(inbox[4].created_at, NOW), /September 12/);
  assert.match(dayLabel(inbox[5].created_at, NOW), /2025/);
  assert.equal(dayLabel('not a date', NOW), 'Unknown date');
});
test('Inbox: requests are grouped by day, newest first, order kept', () => {
  const groups = groupByDay(inbox, NOW);
  assert.deepEqual(groups.map(g => g.label.split(',')[0]), ['Today', 'Yesterday', 'Saturday', 'September 12', 'December 30']);
  assert.deepEqual(groups[0].rows.map(r => r.id), ['a', 'b']);
});
test('Inbox: month summary counts this month by status, plus all time', () => {
  const s = monthSummary(inbox, STATUSES, NOW);
  assert.equal(s.total, 5);
  assert.deepEqual(s.counts, { new: 3, contacted: 1, closed: 1 });
  assert.equal(s.allTime, 6);
  assert.equal(summaryText(s, STATUSES), 'September: 5 requests · 3 new · 1 contacted · 1 closed · 6 all time');
  assert.equal(summaryText(monthSummary([], STATUSES, NOW), STATUSES), '');
});
test('Inbox: opens on New when anything is waiting, otherwise on All', () => {
  assert.equal(defaultFilter(inbox), 'new');
  assert.equal(defaultFilter(inbox.filter(r => r.status !== 'new')), '');
  assert.equal(defaultFilter([]), '');
});
test('Transcript: turns are shown as Caller / Receptionist, blanks and junk dropped', () => {
  const turns = transcriptTurns({ transcript: { turns: [
    { speaker: 'receptionist', text: ' Hi there. ' }, { speaker: 'caller', text: 'Hello' },
    { speaker: 'caller', text: '   ' }, null, { speaker: 'caller' }, { speaker: 'other', text: 'x' },
  ] } });
  assert.deepEqual(turns, [
    { speaker: 'Receptionist', text: 'Hi there.' }, { speaker: 'Caller', text: 'Hello' }, { speaker: 'Receptionist', text: 'x' },
  ]);
  assert.deepEqual(transcriptTurns({ transcript: null }), []);
  assert.deepEqual(transcriptTurns({}), []);
  assert.deepEqual(transcriptTurns({ transcript: { turns: 'nope' } }), []);
});

console.log(`${passed} unit checks passed`);
